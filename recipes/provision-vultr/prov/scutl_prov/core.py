"""Provision core: the guardrail component of recipe #3.

Manifest invariants enforced HERE, in code (recipe.yaml components.prov):
  - no create call leaves the box unless plan ∈ allowlist, region ∈
    allowlist, hourly price ≤ max_hourly_usd, live count < max_instances
  - every created instance is logged before the response returns; the live
    set derives from the log reconciled against the provider
  - destroy is never gated — no approval, no decommission marker, no
    config error may leave a reachable instance running
  - DNS mutations refuse any name outside the configured dns_subzone
  - decommission refuses while log-known instances are live
  - the API key never appears in any return value or log record

This spend is card-funded: the wallet's caps do not see it. These checks
plus provider-side key scoping are the entire enforcement surface — which
is why every one of them runs BEFORE the provider call, not after.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

from . import approvals
from .network import VultrClient
from .state import StateDir


class LimitRefused(Exception):
    """A code-enforced limit said no. Exit 5; never retried around."""


class Manager:
    def __init__(self, state: StateDir | None = None, client: VultrClient | None = None):
        self.state = state or StateDir()
        self.client = client or VultrClient(self.state)

    # -- introspection -------------------------------------------------
    def status(self) -> dict:
        """Limits, key presence, and log-vs-provider reconciliation.
        Works not-configured (reports that) so setup's install step can
        probe the tool before configure."""
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
            out["limits"] = {
                "plans": config["plans"],
                "regions": config["regions"],
                "max_instances": config["max_instances"],
                "max_hourly_usd": config["max_hourly_usd"],
                "dns_subzone": config.get("dns_subzone"),
            }
        if key_present:
            recon = self._reconcile()
            out.update(provider_reachable=True, **recon)
        return out

    def _reconcile(self) -> dict:
        """log_live vs provider list. Foreign instances (provider-only) are
        reported and never touched; lost instances (log-only) are billing
        evidence for the human."""
        log_live = self.state.log_live_ids()
        provider = {i["id"]: i for i in self.client.list_instances()}
        live = [provider[i] for i in log_live if i in provider]
        return {
            "live_instances": len(live),
            "instances": [_public_view(i) for i in live],
            "foreign_instances": sorted(set(provider) - log_live),
            "lost_at_provider": sorted(log_live - set(provider)),
        }

    def list(self) -> dict:
        self.state.load_config()
        return self._reconcile()

    # -- create: every limit checked before the API call -----------------
    def create(self, plan: str, region: str, label: str) -> dict:
        self.state.check_not_decommissioned()
        config = self.state.load_config()
        if plan not in config["plans"]:
            raise LimitRefused(
                f"plan '{plan}' not in allowlist {config['plans']}")
        if region not in config["regions"]:
            raise LimitRefused(
                f"region '{region}' not in allowlist {config['regions']}")
        hourly = self._plan_hourly(plan)
        ceiling = Decimal(config["max_hourly_usd"])
        if hourly > ceiling:
            raise LimitRefused(
                f"plan '{plan}' costs {hourly}/h, over ceiling {ceiling}/h")
        live = self._reconcile()
        if live["live_instances"] >= config["max_instances"]:
            raise LimitRefused(
                f"{live['live_instances']} instances live, "
                f"max_instances is {config['max_instances']}")

        instance = self.client.create_instance(plan, region, label)
        # Log before returning: a created-but-unlogged instance would be
        # invisible to destroy-all and to the reconciliation report.
        self.state.append_instance_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "created",
            "id": instance["id"],
            "plan": plan, "region": region, "label": label,
            "hourly_usd": str(hourly),
        })
        return _public_view(instance)

    def _plan_hourly(self, plan: str) -> Decimal:
        for p in self.client.plans():
            if p["id"] == plan:
                return Decimal(str(p["hourly_cost"]))
        raise LimitRefused(f"plan '{plan}' unknown to the provider; refusing")

    # -- destroy: never gated -------------------------------------------
    def destroy(self, instance_id: str) -> dict:
        """No approval token, no decommission check, no config load beyond
        what the client itself needs. The safe direction stays open."""
        if instance_id not in self.state.log_live_ids():
            raise LimitRefused(
                f"instance '{instance_id}' is not log-known-live; foreign "
                f"instances are never touched (see prov status)")
        self.client.destroy_instance(instance_id)
        self.state.append_instance_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "destroyed",
            "id": instance_id,
        })
        return {"destroyed": instance_id}

    def destroy_all(self) -> dict:
        destroyed = [self.destroy(i)["destroyed"]
                     for i in sorted(self.state.log_live_ids())]
        return {"destroyed": destroyed, "count": len(destroyed)}

    # -- DNS: subzone-fenced ---------------------------------------------
    def _subzone(self) -> str:
        config = self.state.load_config()
        subzone = config.get("dns_subzone")
        if not subzone:
            raise LimitRefused(
                "no dns_subzone configured; DNS writes are disabled")
        return subzone

    def _relative_name(self, name: str, subzone: str) -> str:
        name = name.rstrip(".").lower()
        if name == subzone:
            return ""
        if name.endswith("." + subzone):
            return name[: -len(subzone) - 1]
        raise LimitRefused(
            f"name '{name}' is outside the delegated subzone '{subzone}'")

    def dns_set(self, name: str, rtype: str, value: str) -> dict:
        self.state.check_not_decommissioned()
        subzone = self._subzone()
        rel = self._relative_name(name, subzone)
        record = self.client.create_record(subzone, rel, rtype.upper(), value)
        return {"set": {"name": name, "type": rtype.upper(),
                        "value": value, "record_id": record.get("id")}}

    def dns_delete(self, name: str, rtype: str) -> dict:
        self.state.check_not_decommissioned()
        subzone = self._subzone()
        rel = self._relative_name(name, subzone)
        deleted = []
        for rec in self.client.list_records(subzone):
            if rec["name"] == rel and rec["type"] == rtype.upper():
                self.client.delete_record(subzone, rec["id"])
                deleted.append(rec["id"])
        return {"deleted": deleted, "name": name, "type": rtype.upper()}

    def dns_list(self) -> dict:
        subzone = self._subzone()
        return {"subzone": subzone,
                "records": self.client.list_records(subzone)}

    # -- admin (human-approved) ----------------------------------------
    def configure(self, plans: list[str], regions: list[str],
                  max_instances: int, max_hourly_usd: Decimal,
                  dns_subzone: str | None = None) -> dict:
        approvals.consume(self.state, "configure")
        if max_instances < 1:
            raise ValueError("max_instances must be >= 1")
        if max_hourly_usd <= 0:
            raise ValueError("max_hourly_usd must be > 0")
        self.state.init()
        config = {
            "plans": plans,
            "regions": regions,
            "max_instances": max_instances,
            "max_hourly_usd": str(max_hourly_usd),
        }
        if dns_subzone:
            config["dns_subzone"] = dns_subzone.rstrip(".").lower()
        self.state.save_config(config)
        return {"configured": True, **config}

    def set_key(self, key_file: str) -> dict:
        """Consume a file, never argv — keys do not belong in transcripts.
        The source file is deleted after the move."""
        approvals.consume(self.state, "set-key")
        self.state.init()
        key = open(key_file).read().strip()
        if not key:
            raise ValueError(f"key file '{key_file}' is empty")
        self.state.write_secret(self.state.api_key_file, key.encode())
        os.unlink(key_file)
        return {"key_present": True, "source_removed": key_file}

    def decommission(self) -> dict:
        approvals.consume(self.state, "decommission")
        self.state.load_config()
        live = self.state.log_live_ids()
        if live:
            raise LimitRefused(
                f"{len(live)} instance(s) still live ({sorted(live)}); run "
                f"prov destroy-all first — a marker must not outlive spend")
        marker = {"decommissioned_at": datetime.now(timezone.utc).isoformat()}
        self.state.decommission_marker.write_text(json.dumps(marker))
        return {**marker,
                "note": "API key revocation happens in the provider portal, "
                        "by the human — this marker is not revocation"}


def _public_view(instance: dict) -> dict:
    return {k: instance.get(k) for k in
            ("id", "label", "plan", "region", "status", "power_status",
             "main_ip", "date_created")}
