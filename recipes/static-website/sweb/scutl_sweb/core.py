"""sweb core: the guardrail component of recipe #6.

Manifest invariants enforced HERE, in code (recipe.yaml components.sweb):
  - no create call leaves the box unless the tier price ≤ ceiling AND the
    subscription count < max_subscriptions; a create retry adopts a prior
    success found by list, never doubles
  - s3 credentials live only in state (network strips every provider echo);
    rotate is not 'done' until the OLD pair provably fails
  - public ACLs are per-object, on manifest files only; there is no wider
    scope and no flag that creates one
  - a publish is 'serving', not 'uploaded': per-file public fetch +
    hash-match before any green claim; intent logged before the first byte
  - reconcile joins log manifest vs bucket listing vs public crawl;
    foreign-object and logged-but-absent are named findings
  - site content is data: bytes move and hash; nothing in them is parsed
    for meaning, so nothing in them can steer the tool
  - destroy deletes nothing until an export hash-matches the live
    manifest; 'destroyed' is claimed only from a fresh list

This spend is card-funded: the wallet's caps do not see it. These checks
plus provider-side key scoping are the entire enforcement surface — which
is why every one of them runs BEFORE the provider call, not after.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import approvals
from .network import DataClient, MgmtClient, PermanentError, TransientError
from .state import StateDir

# The publisher owns the MIME map (recon: provider serves whatever
# Content-Type the put declared; wrong type renders as a download).
MIME = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".txt": "text/plain",
    ".md": "text/plain", ".xml": "application/xml",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".ico": "image/x-icon", ".pdf": "application/pdf",
    ".woff": "font/woff", ".woff2": "font/woff2",
}


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class DuplicatePublish(Exception):
    """This publish_id already ran (or crashed in flight). Exit 6;
    reconcile, never blind re-publish."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 mgmt: MgmtClient | None = None,
                 data: DataClient | None = None,
                 edge: object | None = None):
        self.state = state or StateDir()
        self.mgmt = mgmt or MgmtClient(self.state)
        self._data = data  # constructed lazily: needs the subscription's hostname
        # custom-subzone leaf only: the composed prov-rail surface
        # (dns_get/dns_set inside the delegated subzone, instance_ip/
        # instance_up, acme_issue, tls_probe). Injected — sweb never
        # talks to the DNS API or the instance directly.
        self.edge = edge

    def data(self) -> DataClient:
        if self._data is None:
            sub = self.state.load_subscription()
            self._data = DataClient(self.state, sub["s3_hostname"])
        return self._data

    # -- introspection -------------------------------------------------
    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            config = None
        out: dict = {
            "configured": config is not None,
            "key_present": self.state.api_key_file.exists(),
        }
        if config:
            out["walls"] = {
                "monthly_price_ceiling_usd": config["monthly_price_ceiling_usd"],
                "max_subscriptions": config["max_subscriptions"],
                "site_bucket": config["site_bucket"],
                "serving": config["serving"],
                "site_name": config.get("site_name"),
            }
        try:
            sub = self.state.load_subscription()
            out["subscription"] = sub
            out["site_url"] = self.data().public_url(
                config["site_bucket"], "index.html") if config else None
        except Exception:
            out["subscription"] = None
        manifest = self.state.live_manifest()
        out["live_manifest_files"] = len(manifest)
        out["unresolved_publishes"] = self.state.unresolved_intents()
        return out

    # -- provision: every wall checked before the API call ---------------
    def provision(self, cluster_id: int) -> dict:
        config = self.state.load_config()
        label = f"sweb:{config['site_bucket']}"
        try:
            self.state.load_subscription()
            raise LimitRefused(
                "a subscription is already provisioned (see sweb status); "
                "rev 1 holds exactly one")
        except LimitRefused:
            raise
        except Exception:
            pass

        # Adopt-before-create: a transient failure on a prior attempt may
        # have created the subscription anyway — and a 500 may have created
        # nothing. The list decides, not assumption.
        existing = [s for s in self.mgmt.list() if s.get("label") == label]
        if existing:
            sub = existing[0]
            record = _sub_record(sub, cluster_id, adopted=True)
            self.state.save_subscription(record)
            self.state.append_event({
                "ts": _now(), "event": "provision", "adopted": True,
                "id": sub["id"]})
            return {"adopted": True, **record}

        ceiling = Decimal(config["monthly_price_ceiling_usd"])
        if len(self.mgmt.list()) >= config["max_subscriptions"]:
            raise LimitRefused(
                f"{config['max_subscriptions']} subscription(s) already exist "
                f"at the provider, max_subscriptions is "
                f"{config['max_subscriptions']} — reuse or owner-decided "
                f"teardown, never a second subscription")
        tiers = sorted(self.mgmt.cluster_tiers(cluster_id),
                       key=lambda t: Decimal(str(t["price"])))
        if not tiers:
            raise LimitRefused(f"cluster {cluster_id} offers no tiers")
        tier = tiers[0]
        price = Decimal(str(tier["price"]))
        if price > ceiling:
            raise LimitRefused(
                f"cheapest tier '{tier.get('slug', tier['id'])}' costs "
                f"{price}/mo, over ceiling {ceiling}/mo")

        sub = self.mgmt.create(cluster_id, tier["id"], label)
        record = _sub_record(sub, cluster_id, tier=tier)
        # Log before returning: a created-but-unlogged subscription is
        # invisible billing.
        self.state.save_subscription(record)
        self.state.append_event({
            "ts": _now(), "event": "provision", "id": sub["id"],
            "tier": tier["id"], "monthly_usd": str(price)})
        return record

    # -- publish: intent first, verify before any green claim ------------
    def publish(self, publish_id: str, source: str) -> dict:
        config = self.state.load_config()
        self.state.load_subscription()
        if publish_id in self.state.publish_ids():
            raise DuplicatePublish(
                f"publish_id '{publish_id}' already ran or crashed in "
                f"flight; run 'sweb log --reconcile' first")
        bucket = config["site_bucket"]
        manifest = _walk_manifest(Path(source))
        self.state.append_event({
            "ts": _now(), "event": "publish-intent",
            "publish_id": publish_id, "manifest": manifest})

        served, failed = [], []
        for f in manifest:
            body = (Path(source) / f["key"]).read_bytes()
            try:
                self.data().put(bucket, f["key"], body,
                                f["content_type"], public=True)
            except (TransientError, PermanentError) as e:
                failed.append({"key": f["key"], "stage": "put", "why": str(e)})
                continue
            verdict = self._verify_one(bucket, f)
            (served if verdict is None else failed).append(
                f["key"] if verdict is None
                else {"key": f["key"], "stage": "verify", "why": verdict})

        ok = not failed
        self.state.append_event({
            "ts": _now(), "event": "publish-outcome",
            "publish_id": publish_id, "ok": ok, "served": served,
            "failed": failed})
        return {"publish_id": publish_id, "serving": ok,
                "served": served, "failed": failed,
                "site_url": self.data().public_url(bucket, "index.html")}

    def _verify_one(self, bucket: str, f: dict) -> str | None:
        """None = serving and byte-true; else the named reason."""
        try:
            body, ctype = self.data().public_get(bucket, f["key"])
        except PermanentError as e:
            return f"not publicly serving ({e})"
        except TransientError as e:
            return f"undetermined (transient: {e})"
        if hashlib.sha256(body).hexdigest() != f["sha256"]:
            return "served bytes do not hash-match the source"
        if ctype.split(";")[0].strip() != f["content_type"]:
            return (f"served Content-Type '{ctype}' != "
                    f"declared '{f['content_type']}'")
        return None

    def verify(self) -> dict:
        config = self.state.load_config()
        bucket = config["site_bucket"]
        manifest = self.state.live_manifest()
        if not manifest:
            raise LimitRefused("nothing published yet; nothing to verify")
        failures = []
        for key, f in sorted(manifest.items()):
            verdict = self._verify_one(bucket, {"key": key, **f})
            if verdict is not None:
                failures.append({"key": key, "why": verdict})
        return {"files": len(manifest), "serving": not failures,
                "failures": failures}

    # -- rotate: not done until the old pair is dead ----------------------
    def rotate(self) -> dict:
        sub = self.state.load_subscription()
        config = self.state.load_config()
        old = self.state.load_s3_keys()
        self.mgmt.regenerate_keys(sub["id"])  # new pair -> state via network
        old_dead = False
        try:
            self.data().list(config["site_bucket"], creds=old)
        except (PermanentError, TransientError):
            old_dead = True
        self.state.append_event({
            "ts": _now(), "event": "rotate", "old_pair_dead": old_dead})
        return {"rotated": True, "old_pair_dead": old_dead,
                **({} if old_dead else {"warning":
                    "OLD PAIR STILL ANSWERS — rotation is not complete; "
                    "report this, do not proceed as if rotated"})}

    # -- reconcile: log vs bucket vs public crawl -------------------------
    def reconcile(self) -> dict:
        config = self.state.load_config()
        bucket = config["site_bucket"]
        manifest = self.state.live_manifest()
        listed = set(self.data().list(bucket))
        findings = []
        for key in sorted(listed - set(manifest)):
            findings.append({"finding": "foreign-object", "key": key,
                             "detail": "live in bucket, in no publish log "
                                       "entry — defacement or foreign write"})
        for key in sorted(set(manifest) - listed):
            findings.append({"finding": "logged-but-absent", "key": key,
                             "detail": "publish log says serving, bucket "
                                       "listing lacks it"})
        for key, f in sorted(manifest.items()):
            if key not in listed:
                continue
            verdict = self._verify_one(bucket, {"key": key, **f})
            if verdict is not None:
                findings.append({"finding": "serving-divergence",
                                 "key": key, "detail": verdict})
        for pid in self.state.unresolved_intents():
            findings.append({"finding": "unresolved-publish",
                             "publish_id": pid,
                             "detail": "intent logged, no outcome — crash "
                                       "in flight; per-file state above"})
        return {"clean": not findings, "files": len(manifest),
                "findings": findings}

    def log(self) -> dict:
        return {"events": self.state.read_events()}

    # -- destroy: export-verify first, billing-stop verified after --------
    def destroy(self, export_dir: str) -> dict:
        sub = self.state.load_subscription()
        config = self.state.load_config()
        bucket = config["site_bucket"]
        manifest = self.state.live_manifest()
        exported, mismatched = [], []
        out = Path(export_dir)
        for key, f in sorted(manifest.items()):
            try:
                body, _ = self.data().public_get(bucket, key)
            except (PermanentError, TransientError) as e:
                mismatched.append({"key": key, "why": f"fetch failed: {e}"})
                continue
            if hashlib.sha256(body).hexdigest() != f["sha256"]:
                mismatched.append({"key": key, "why": "hash mismatch"})
                continue
            dest = out / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            exported.append(key)
        if mismatched:
            raise LimitRefused(
                f"export did not verify ({len(mismatched)} file(s): "
                f"{json.dumps(mismatched)}); refusing to delete the site's "
                f"only copy")

        self.mgmt.delete(sub["id"])
        still = [s for s in self.mgmt.list() if s.get("id") == sub["id"]]
        if still:
            self.state.append_event({
                "ts": _now(), "event": "destroy-undetermined", "id": sub["id"]})
            return {"destroyed": False, "id": sub["id"],
                    "warning": "delete accepted but a fresh list still shows "
                               "the subscription — billing not verifiably "
                               "stopped; report, do not re-delete blindly"}
        self.state.append_event({
            "ts": _now(), "event": "destroy", "id": sub["id"],
            "exported": len(exported), "export_dir": str(out)})
        self.state.clear_subscription()
        return {"destroyed": True, "id": sub["id"],
                "exported": exported, "export_dir": str(out),
                "billing_stopped_verified_by": "fresh subscription list"}

    # -- custom-subzone edge (composed prov rail) ----------------------
    def _edge_name(self) -> str:
        config = self.state.load_config()
        if config.get("serving") != "custom-subzone":
            raise LimitRefused(
                "edge ops apply only to serving=custom-subzone; this "
                "install serves from the provider domain")
        if self.edge is None:
            raise LimitRefused(
                "no edge wired: custom-subzone serving composes the "
                "provision rail (instance + DNS + ACME); wire it first")
        return config["site_name"]

    def edge_attach(self) -> dict:
        """Point the site name at the edge instance and issue its cert.

        One DNS set + ONE ACME issuance attempt per call. An ACME
        rate-limit is a TransientError the caller must report and wait
        out — never loop this op against a limit.
        """
        name = self._edge_name()
        ip = self.edge.instance_ip()
        self.edge.dns_set(name, ip)
        cert = self.edge.acme_issue(name)
        self.state.append_event({
            "ts": _now(), "event": "edge-attach", "name": name, "ip": ip})
        return {"attached": True, "name": name, "ip": ip, "cert": cert}

    def edge_status(self) -> dict:
        """Facts only: DNS answer, instance health, cert expiry, and
        whether the CONTENT is still safe on the bucket regardless of
        edge health (loss vs outage are different emergencies)."""
        name = self._edge_name()
        config = self.state.load_config()
        out: dict = {"name": name,
                     "dns_ip": self.edge.dns_get(name),
                     "instance_up": bool(self.edge.instance_up())}
        try:
            out["cert"] = self.edge.tls_probe(name)
        except (PermanentError, TransientError) as e:
            out["cert"] = {"error": str(e)}
        manifest = self.state.live_manifest()
        if manifest:
            key = sorted(manifest)[0]
            try:
                body, _ = self.data().public_get(config["site_bucket"], key)
                ok = hashlib.sha256(body).hexdigest() == manifest[key]["sha256"]
                out["content_safe_on_bucket"] = ok
            except (PermanentError, TransientError) as e:
                out["content_safe_on_bucket"] = False
                out["content_probe_error"] = str(e)
        else:
            out["content_safe_on_bucket"] = None
        return out

    # -- admin (human-approved) ----------------------------------------
    def configure(self, ceiling_usd: Decimal, max_subscriptions: int,
                  site_bucket: str, serving: str,
                  site_name: str | None = None) -> dict:
        approvals.consume(self.state, "configure")
        if ceiling_usd <= 0:
            raise ValueError("monthly_price_ceiling_usd must be > 0")
        if max_subscriptions < 1:
            raise ValueError("max_subscriptions must be >= 1")
        if serving not in ("provider-domain", "custom-subzone"):
            raise ValueError("serving must be provider-domain|custom-subzone")
        if serving == "custom-subzone" and not site_name:
            raise ValueError("custom-subzone needs site_name")
        self.state.init()
        config = {
            "monthly_price_ceiling_usd": str(ceiling_usd),
            "max_subscriptions": max_subscriptions,
            "site_bucket": site_bucket,
            "serving": serving,
        }
        if site_name:
            config["site_name"] = site_name.rstrip(".").lower()
        self.state.save_config(config)
        return {"configured": True, **config}

    def set_key(self, key_file: str) -> dict:
        """Consume a file, never argv — keys do not belong in transcripts."""
        approvals.consume(self.state, "set-key")
        self.state.init()
        key = open(key_file).read().strip()
        if not key:
            raise ValueError(f"key file '{key_file}' is empty")
        self.state.write_secret(self.state.api_key_file, key.encode())
        os.unlink(key_file)
        return {"key_present": True, "source_removed": key_file}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sub_record(sub: dict, cluster_id: int, tier: dict | None = None,
                adopted: bool = False) -> dict:
    return {
        "id": sub["id"], "cluster_id": cluster_id,
        "s3_hostname": sub.get("s3_hostname"),
        "label": sub.get("label"), "status": sub.get("status"),
        **({"tier": tier["id"], "monthly_usd": str(tier["price"])}
           if tier else {}),
    }


def _walk_manifest(source: Path) -> list[dict]:
    """Relative keys under the source dir only. Symlinks are refused —
    a link out of the tree is a write outside the declared site root by
    other means. Content is hashed, never parsed: bytes are data."""
    if not source.is_dir():
        raise LimitRefused(f"source '{source}' is not a directory")
    manifest = []
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            raise LimitRefused(
                f"'{path}' is a symlink; the site root is the declared "
                f"tree, links out of it are refused")
        key = path.relative_to(source).as_posix()
        body = path.read_bytes()
        manifest.append({
            "key": key,
            "sha256": hashlib.sha256(body).hexdigest(),
            "size": len(body),
            "content_type": MIME.get(path.suffix.lower(),
                                     "application/octet-stream"),
        })
    if not manifest:
        raise LimitRefused(f"source '{source}' is empty; nothing to publish")
    return manifest
