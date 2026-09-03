"""gpod core: the guardrail component of recipe #13.

Manifest invariants enforced HERE, in code (recipe.yaml components.gpod):
  - no create call leaves the box unless gpu_type ∈ allowlist, catalog
    price ≤ max_hourly_usd, and log-reconciled live pods < max_pods
  - every created pod is in rentals.log before create returns; the live
    set derives from the log reconciled against the provider; a
    provider pod the log does not know is FOREIGN — reported, never
    touched, destroy-all included
  - destroy is never gated; a destroy that cannot verify gone within
    its retry bound raises Undead (exit 6) with billing-may-be-accruing
    language and leaves the rental OPEN in the log
  - the network volume is attach-only: its id comes from ratified
    config, no tool creates/grows/destroys it, and its standing cost
    appears in every status
  - the API key never appears in any return value or log record
  - the create checklist that bit a run night each lives in code, not
    prose: devel-image family only, 22/tcp at create, no
    dockerStartCmd/dockerEntrypoint override
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

from . import approvals
from .network import Absent, PodsClient, StockClient
from .state import StateDir

# RunPod network storage list price (USD/GB-month, secure cloud,
# 2026-09 console). An ESTIMATE for visibility, not billing truth —
# the invoice is authoritative. Named in status so standing spend is
# never ambient (manifest: volume-custody).
VOLUME_USD_PER_GB_MONTH = Decimal("0.07")

DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
DESTROY_VERIFY_ATTEMPTS = 12
DESTROY_VERIFY_INTERVAL = 5.0


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class Undead(Exception):
    """destroy could not verify gone. Exit 6; an emergency to surface."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 pods: PodsClient | None = None,
                 stock: StockClient | None = None,
                 sleep_fn=time.sleep):
        self.state = state or StateDir()
        self.pods = pods or PodsClient(self.state)
        self.stock_client = stock or StockClient(self.state)
        self._sleep = sleep_fn

    # -- introspection -------------------------------------------------
    def status(self) -> dict:
        """Walls, key presence, volume with its standing cost, and
        log-vs-provider reconciliation. Works not-configured (reports
        that) so setup's install step can probe before configure."""
        key_present = self.state.api_key_file.exists()
        try:
            config = self.state.load_config()
        except Exception:
            config = None
        out: dict = {
            "configured": config is not None,
            "key_present": key_present,
            "decommissioned": self.state.decommission_marker.exists(),
        }
        if config:
            out["walls"] = {
                "gpu_types": config["gpu_types"],
                "max_hourly_usd": config["max_hourly_usd"],
                "max_pods": config["max_pods"],
                "region_pin": config["region_pin"],
            }
            out["volume"] = self._volume_view(config)
        if key_present:
            recon = self._reconcile()
            out.update(provider_reachable=True, **recon)
        return out

    def _volume_view(self, config: dict) -> dict | None:
        vol = config.get("volume")
        if not vol:
            return None
        view = dict(vol)
        size = vol.get("size_gb")
        if size is not None:
            view["monthly_usd_estimate"] = str(
                (Decimal(size) * VOLUME_USD_PER_GB_MONTH).quantize(
                    Decimal("0.01")))
            view["note"] = ("standing spend, accrues pod or no pod; "
                            "attach-only — lifecycle is a human console act")
        return view

    def _reconcile(self) -> dict:
        """Open rentals vs provider list. Foreign pods (provider-only)
        are reported and never touched; lost pods (log-open,
        provider-absent) are billing evidence begging a verified close."""
        open_rentals = self.state.open_rentals()
        provider = {p["id"]: p for p in self.pods.list_pods()}
        now = datetime.now(timezone.utc)
        live = []
        for pod_id, rec in open_rentals.items():
            if pod_id in provider:
                view = _public_view(provider[pod_id])
                started = datetime.fromisoformat(rec["ts"])
                hours = (now - started).total_seconds() / 3600
                view["rented_hours"] = round(hours, 2)
                try:
                    view["accrued_usd_estimate"] = str(
                        (Decimal(rec["hourly_usd"]) *
                         Decimal(str(round(hours, 4)))).quantize(
                            Decimal("0.01")))
                except Exception:
                    pass
                live.append(view)
        return {
            "live_pods": len(live),
            "pods": live,
            "foreign_pods": sorted(set(provider) - set(open_rentals)),
            "open_but_absent": sorted(set(open_rentals) - set(provider)),
        }

    def list(self) -> dict:
        self.state.load_config()
        return self._reconcile()

    def stock(self, gpu_type: str | None = None) -> dict:
        config = self.state.load_config()
        gpu = gpu_type or (config["gpu_types"][0] if config["gpu_types"]
                           else None)
        if gpu is None:
            raise ValueError("no gpu type given and allowlist is empty")
        out = self.stock_client.availability(gpu, config.get("region_pin"))
        price = self.stock_client.catalog_price(gpu)
        out["catalog_hourly_usd"] = str(price) if price is not None else None
        return out

    # -- create: every wall checked before the API call ----------------
    def create(self, gpu_type: str, name: str,
               image: str | None = None) -> dict:
        self.state.check_not_decommissioned()
        config = self.state.load_config()

        if gpu_type not in config["gpu_types"]:
            raise LimitRefused(
                f"gpu type '{gpu_type}' not in allowlist "
                f"{config['gpu_types']}")

        price = self.stock_client.catalog_price(gpu_type)
        if price is None:
            raise LimitRefused(
                f"catalog has no secure-cloud price for '{gpu_type}'; an "
                f"unpriceable type cannot be checked against the ceiling")
        ceiling = Decimal(config["max_hourly_usd"])
        if price > ceiling:
            raise LimitRefused(
                f"'{gpu_type}' costs {price}/h, over ceiling {ceiling}/h")

        recon = self._reconcile()
        if recon["live_pods"] >= config["max_pods"]:
            raise LimitRefused(
                f"{recon['live_pods']} pod(s) live, max_pods is "
                f"{config['max_pods']}")

        image = image or DEFAULT_IMAGE
        # Checklist wall (manifest invariant: each item bit a run night
        # once). devel-family only — nvidia/cuda base images boot with
        # no networking on this provider.
        if "-devel" not in image:
            raise LimitRefused(
                f"image '{image}' is not a -devel family image; "
                f"non-devel images have booted with no networking "
                f"(POD-RUNBOOK.md); refusing")

        spec = {
            "name": name,
            "cloudType": "SECURE",
            "computeType": "GPU",
            "gpuTypeIds": [gpu_type],
            "gpuCount": 1,
            "imageName": image,
            "containerDiskInGb": 60,
            "ports": ["22/tcp"],   # immutable after create; declared now
            # deliberately NO dockerStartCmd / dockerEntrypoint:
            # overriding eats /start.sh and sshd never starts
        }
        vol = config.get("volume")
        if vol:
            spec["networkVolumeId"] = vol["id"]

        pod = self.pods.create_pod(spec)
        # Log before returning: a created-but-unlogged pod would be
        # invisible to destroy-all and bill until a human notices.
        self.state.append_rental_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "created",
            "id": pod["id"],
            "gpu_type": gpu_type, "name": name, "image": image,
            "hourly_usd": str(price),
        })
        out = _public_view(pod)
        out["hourly_usd"] = str(price)
        return out

    # -- destroy: never gated, verified or screaming -------------------
    def destroy(self, pod_id: str) -> dict:
        """No approval token, no decommission check. Issues the DELETE,
        then re-reads until absent (bounded). Present after the bound:
        Undead — the rental stays OPEN in the log and status keeps
        ringing until a verified close."""
        open_rentals = self.state.open_rentals()
        if pod_id not in open_rentals:
            raise LimitRefused(
                f"pod '{pod_id}' is not an open rental in the log; "
                f"foreign pods are never touched (see gpod status). If "
                f"this is a lost rental already gone at the provider, "
                f"it is not in the log either — check gpod status "
                f"open_but_absent")
        self.state.append_rental_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "destroy-requested", "id": pod_id,
        })
        self.pods.delete_pod(pod_id)
        for attempt in range(DESTROY_VERIFY_ATTEMPTS):
            try:
                self.pods.get_pod(pod_id)
            except Absent:
                self.state.append_rental_event({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "destroy-verified", "id": pod_id,
                })
                return {"destroyed": pod_id, "verified_gone": True,
                        "verify_reads": attempt + 1}
            if attempt < DESTROY_VERIFY_ATTEMPTS - 1:
                self._sleep(DESTROY_VERIFY_INTERVAL)
        raise Undead(
            f"pod '{pod_id}': destroy requested but the provider still "
            f"shows it after {DESTROY_VERIFY_ATTEMPTS} reads — UNDEAD; "
            f"billing may still be accruing — escalate to the owner. "
            f"The rental stays open in the log; after a manual console "
            f"destroy, run gpod destroy --id {pod_id} again to record "
            f"verified-gone")

    def destroy_all(self) -> dict:
        """Every OPEN RENTAL, in order; foreign pods are excluded by
        construction (ruled 2026-09-03: never touched, destroy-all
        included). First Undead aborts the sweep loudly — a silent
        partial success would green-wash the bill."""
        results = [self.destroy(pod_id)
                   for pod_id in sorted(self.state.open_rentals())]
        return {"destroyed": [r["destroyed"] for r in results],
                "count": len(results)}

    # -- admin (human-approved) ----------------------------------------
    def configure(self, gpu_types: list[str], max_hourly_usd: Decimal,
                  max_pods: int, region_pin: str,
                  volume_id: str | None = None) -> dict:
        approvals.consume(self.state, "configure")
        if not gpu_types:
            raise ValueError("gpu_types allowlist must not be empty")
        if max_hourly_usd <= 0:
            raise ValueError("max_hourly_usd must be > 0")
        if max_pods < 1:
            raise ValueError("max_pods must be >= 1")
        self.state.init()
        config = {
            "gpu_types": gpu_types,
            "max_hourly_usd": str(max_hourly_usd),
            "max_pods": max_pods,
            "region_pin": region_pin,
        }
        if volume_id:
            vol: dict = {"id": volume_id}
            # Best-effort enrichment so status can name the standing
            # cost; a failure here must not block ratifying the walls.
            try:
                import json as _json
                from .network import _http
                info = _http(f"{self.pods.base}/networkvolumes/{volume_id}",
                             self.state.load_api_key())
                vol.update(name=info.get("name"),
                           size_gb=info.get("size"),
                           data_center=info.get("dataCenterId"))
                if (info.get("dataCenterId")
                        and info["dataCenterId"] != region_pin):
                    raise ValueError(
                        f"volume '{volume_id}' lives in "
                        f"{info['dataCenterId']} but region_pin is "
                        f"{region_pin} — an unpinned create cannot "
                        f"attach it; fix the pin")
            except ValueError:
                raise
            except Exception:
                vol["note"] = "volume metadata unreadable at configure time"
            config["volume"] = vol
        self.state.save_config(config)
        return {"configured": True, **config}

    def set_key(self, key_file: str) -> dict:
        """Consume a file, never argv — keys do not belong in
        transcripts. The source file is deleted after the move."""
        import os
        approvals.consume(self.state, "set-key")
        self.state.init()
        key = open(key_file).read().strip()
        if not key:
            raise ValueError(f"key file '{key_file}' is empty")
        self.state.write_secret(self.state.api_key_file, key.encode())
        os.unlink(key_file)
        return {"key_present": True, "source_removed": key_file}

    def decommission(self) -> dict:
        import json as _json
        self.state.load_config()
        open_rentals = self.state.open_rentals()
        # Wall before token: a refusal must not silently spend the
        # human's consent (they granted decommission, not a retry loop).
        if open_rentals:
            raise LimitRefused(
                f"{len(open_rentals)} rental(s) still open "
                f"({sorted(open_rentals)}); run gpod destroy-all first — "
                f"a marker must not outlive spend")
        approvals.consume(self.state, "decommission")
        marker = {"decommissioned_at":
                  datetime.now(timezone.utc).isoformat()}
        self.state.decommission_marker.write_text(_json.dumps(marker))
        return {**marker,
                "note": "API key revocation happens in the RunPod "
                        "console, by the human — this marker is not "
                        "revocation. destroy still works."}


def _public_view(pod: dict) -> dict:
    return {k: pod.get(k) for k in
            ("id", "name", "desiredStatus", "costPerHr", "machineId",
             "publicIp", "portMappings", "gpuCount", "imageName",
             "lastStartedAt")}
