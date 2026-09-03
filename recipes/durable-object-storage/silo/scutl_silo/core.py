"""silo core: the guardrail component of recipe #9.

Manifest invariants enforced HERE, in code (recipe.yaml components.silo):
  - a put is not done until the read-back re-hash matches the digest
    computed BEFORE upload; only a match appends to the manifest, and
    provider metadata (ETag) is recorded as advisory, never trusted
  - writes are append-shaped: puts never address an existing key,
    deletion and teardown are approval-gated consented acts, and an
    over-cap put PARKS — silently deleting old backups to make room
    has no code path
  - key material never rides: the deny-list hard-refuses at put, and
    the refusal lands in the manifest log as evidence
  - the word 'restorable' has exactly one source: the rehearsal
    ledger; reports quote it, never memory, and an overdue or missing
    rehearsal is a named breach (deafness doctrine at the schedule)
  - escalation is STRUCTURAL: red/overdue rehearsal, inventory drift,
    cap breach, unreachable endpoint, and spend anomaly each append a
    named breach, and escalate=true derives from the breaches list
  - teardown is done when the rail says gone AND the store stops
    answering our keys; an undead subscription FAILS the teardown

Restored bytes are data at full width: nothing in this module parses,
executes, or routes on backup content — bytes go in hashed, come out
re-hashed, and get quoted, never obeyed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import approvals
from .state import StateDir
from .store import MissingObject, ObjectStore, Rail, StoreUnreachable

GB = 1_000_000_000          # decimal, matching provider pricing
MANIFEST_COPY_KEY = "_meta/manifest.jsonl"   # the one mutable key: the
# riding copy for disaster symmetry. It attests nothing about itself —
# the LOCAL manifest is authoritative (recipe.yaml invariant 2).

# Paths that never ride, regardless of config (the #3 boundary). The
# config's deny_globs ADD to these; nothing can subtract.
DENY_BUILTIN = ("*.key", "api.key", "s3.creds", "*.pem", "*.kek",
                "*secret*", "*custody*", "id_rsa*", "id_ed25519*")

WALLS = ("storage_cap_gb", "monthly_spend_cap_usd",
         "rehearsal_interval_days", "rehearsal_horizon_factor",
         "single_put_limit_mb")


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class DenyListed(LimitRefused):
    """Key material offered for backup. The #3 boundary, as an error."""


class IntegrityError(Exception):
    """Bytes or provider state failed verification. Loud by design."""


class UnknownKey(Exception):
    """No such logical key in the manifest inventory."""


class NotProvisioned(Exception):
    """No endpoint/creds yet; run 'silo admin provision' first."""


class WallsUnratified(Exception):
    """The five walls are not all set; run 'silo admin configure' first."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 store: ObjectStore | None = None,
                 rail: Rail | None = None, now_fn=None):
        self.state = state or StateDir()
        self._injected_store = store
        self._rail = rail
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- substrate access -------------------------------------------------
    def _store(self, config: dict) -> ObjectStore:
        if self._injected_store is not None:
            return self._injected_store
        if not config.get("endpoint"):
            raise NotProvisioned("no endpoint in config")
        creds = self.state.load_creds()
        if not creds:
            raise NotProvisioned(str(self.state.creds_file))
        from .s3live import S3Store
        return S3Store(config["endpoint"], config.get("bucket", "silo"),
                       creds["access"], creds["secret"],
                       region=config.get("region", "us-east-1"),
                       now_fn=self._now)

    def _walls(self, config: dict) -> dict:
        missing = [w for w in WALLS if w not in config]
        if missing:
            raise WallsUnratified(
                f"unratified: {', '.join(missing)} — all five walls are "
                f"owner-set before the first put (recipe.yaml setup)")
        return {w: int(config[w]) for w in WALLS}

    # -- the manifest, derived --------------------------------------------
    def _inventory(self) -> dict[str, dict]:
        inv: dict[str, dict] = {}
        for e in self.state.read_manifest():
            if e.get("event") == "put":
                inv[e["key"]] = e
            elif e.get("event") == "delete":
                inv.pop(e["key"], None)
        return inv

    @staticmethod
    def _store_keys(entry: dict) -> list[str]:
        if entry.get("chunks"):
            return [c["key"] for c in entry["chunks"]]
        return [entry["key"]]

    def _bytes_stored(self, inv: dict[str, dict]) -> int:
        return sum(e["size"] for e in inv.values())

    def _projection(self, config: dict, walls: dict, total_bytes: int) -> dict:
        prices = config.get("prices", {})
        gb = total_bytes / GB
        base = float(prices.get("base_usd", 6.0))
        storage = gb * float(prices.get("disk_gb_usd", 0.006))
        rehearsals_per_month = 30.0 / max(1, walls["rehearsal_interval_days"])
        egress = gb * float(prices.get("bw_gb_usd", 0.01)) * rehearsals_per_month
        return {"base_usd": round(base, 4),
                "storage_usd": round(storage, 4),
                "rehearsal_egress_usd": round(egress, 4),
                "total_usd": round(base + storage + egress, 4),
                "bytes": total_bytes}

    # -- deny-list (the #3 boundary) ---------------------------------------
    def _deny_reason(self, config: dict, path: Path) -> str | None:
        globs = tuple(config.get("deny_globs", ())) + DENY_BUILTIN
        probes = [path.name, str(path)] + [part for part in path.parts]
        for g in globs:
            for probe in probes:
                if fnmatch.fnmatch(probe.lower(), g.lower()):
                    return g
        return None

    # -- put: hash, wall-check, upload, read back, re-hash, record ---------
    def put(self, path: str, set_name: str = "default") -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise ValueError(f"not a file: {p}")
        denied = self._deny_reason(config, p)
        if denied:
            # a refusal is evidence, not an omission (recipe.yaml
            # invariant 4): it lands in the log before the exception
            self.state.append_manifest({
                "ts": self._now().isoformat(), "event": "refused",
                "reason": "deny-list", "glob": denied, "source": str(p)})
            raise DenyListed(
                f"'{p}' matches deny glob '{denied}' — key material and "
                f"secret paths never ride (recipe #3 boundary); backups "
                f"of secrets are a human-performed, offline act")
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        inv = self._inventory()

        # caps BEFORE any byte moves; a park is loud and logged
        projected_bytes = self._bytes_stored(inv) + len(data)
        cap_bytes = walls["storage_cap_gb"] * GB
        proj = self._projection(config, walls, projected_bytes)
        park_why = None
        if projected_bytes > cap_bytes:
            park_why = (f"storage cap: {projected_bytes} bytes projected "
                        f"exceeds storage_cap_gb={walls['storage_cap_gb']}")
        elif proj["total_usd"] > walls["monthly_spend_cap_usd"]:
            park_why = (f"spend cap: projected "
                        f"${proj['total_usd']}/mo exceeds "
                        f"monthly_spend_cap_usd={walls['monthly_spend_cap_usd']}")
        if park_why:
            self.state.append_manifest({
                "ts": self._now().isoformat(), "event": "parked",
                "reason": park_why, "source": str(p), "size": len(data)})
            raise LimitRefused(
                f"{park_why} — the put PARKS for owner consent; deleting "
                f"old backups to fit is not an exit (projection: {proj})")

        key = (f"{set_name}/{self._now().strftime('%Y%m%dT%H%M%SZ')}"
               f"-{sha[:12]}/{p.name}")
        store = self._store(config)
        limit = walls["single_put_limit_mb"] * 1024 * 1024
        parts: list[tuple[str, bytes]]
        if len(data) > limit:
            chunks = [data[i:i + limit] for i in range(0, len(data), limit)]
            parts = [(f"{key}.p{i:04d}", c) for i, c in enumerate(chunks)]
        else:
            parts = [(key, data)]

        for pkey, _ in parts:
            if pkey in inv or store.exists(pkey):
                raise LimitRefused(
                    f"key '{pkey}' already exists — puts never overwrite; "
                    f"deletion is a separate consented act")

        written: list[str] = []
        advisory = None
        try:
            for pkey, pdata in parts:
                store.put(pkey, pdata)
                written.append(pkey)
                try:
                    back = store.get(pkey)
                except MissingObject:
                    raise IntegrityError(
                        f"phantom write: '{pkey}' was acked but is not "
                        f"there to read back — the put FAILED; nothing "
                        f"enters the manifest")
                got = hashlib.sha256(back).hexdigest()
                want = hashlib.sha256(pdata).hexdigest()
                if got != want:
                    raise IntegrityError(
                        f"read-back mismatch on '{pkey}': sent sha256 "
                        f"{want}, got {got} ({len(back)} bytes back for "
                        f"{len(pdata)} sent) — the put FAILED; nothing "
                        f"enters the manifest")
            # advisory only: single-part ETag should be the MD5. A liar
            # here changes nothing (bytes already re-verified) but is
            # worth recording as the liar it was (metadata-lies cell).
            if len(parts) == 1:
                try:
                    etag = store.head(key).get("etag")
                    advisory = {"etag": etag,
                                "md5_match": (etag == hashlib.md5(data)
                                              .hexdigest()) if etag else None}
                except (MissingObject, StoreUnreachable):
                    advisory = {"etag": None, "md5_match": None}
        except IntegrityError:
            for wkey in written:   # never-manifested bytes, best-effort
                try:
                    store.delete(wkey)
                except (MissingObject, StoreUnreachable):
                    pass
            self.state.append_manifest({
                "ts": self._now().isoformat(), "event": "put-failed",
                "key": key, "source": str(p), "size": len(data)})
            raise

        record = {
            "ts": self._now().isoformat(), "event": "put", "key": key,
            "set": set_name, "source": str(p), "sha256": sha,
            "size": len(data),
            "chunks": ([{"key": k, "sha256":
                         hashlib.sha256(d).hexdigest(), "size": len(d)}
                        for k, d in parts] if len(parts) > 1 else None),
            "advisory": advisory}
        self.state.append_manifest(record)
        self._ride_manifest(store)
        return {"stored": key, "sha256": sha, "size": len(data),
                "chunks": len(parts) if len(parts) > 1 else None,
                "advisory": advisory, "projection": proj}

    def _ride_manifest(self, store: ObjectStore) -> None:
        """The copy that rides along. Best-effort by design: the local
        manifest is authoritative, and a bucket that cannot take the
        copy will fail louder walls than this one."""
        try:
            store.put(MANIFEST_COPY_KEY,
                      self.state.manifest_log.read_bytes())
        except (StoreUnreachable, OSError):
            pass

    # -- get: restore into scratch, never over live state -------------------
    def get(self, key: str, into: str) -> dict:
        config = self.state.load_config()
        entry = self._inventory().get(key)
        if entry is None:
            raise UnknownKey(key)
        dest_dir = Path(into).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(key).name
        if dest.exists():
            raise LimitRefused(
                f"'{dest}' already exists — restores never overwrite, "
                f"even in scratch; point --into at a clean directory")
        store = self._store(config)
        blob, mismatches = self._fetch_entry(store, entry)
        dest.write_bytes(blob)
        got = hashlib.sha256(blob).hexdigest()
        verified = not mismatches and got == entry["sha256"]
        return {"restored": str(dest), "verified": verified,
                "sha256_expected": entry["sha256"], "sha256_actual": got,
                "mismatches": mismatches}

    def _fetch_entry(self, store: ObjectStore, entry: dict
                     ) -> tuple[bytes, list[dict]]:
        """Fetch one logical object; per-chunk digest evidence, bytes
        returned verbatim regardless (evidence beats absence)."""
        mismatches: list[dict] = []
        blob = b""
        chunks = entry.get("chunks") or [
            {"key": entry["key"], "sha256": entry["sha256"],
             "size": entry["size"]}]
        for c in chunks:
            try:
                part = store.get(c["key"])
            except MissingObject:
                mismatches.append({"key": c["key"], "problem": "missing"})
                continue
            got = hashlib.sha256(part).hexdigest()
            if len(part) != c["size"]:
                mismatches.append({
                    "key": c["key"], "problem": "size",
                    "expected": c["size"], "actual": len(part)})
            elif got != c["sha256"]:
                mismatches.append({
                    "key": c["key"], "problem": "digest",
                    "expected": c["sha256"], "actual": got})
            blob += part
        return blob, mismatches

    # -- the spine: rehearse ------------------------------------------------
    def rehearse(self) -> dict:
        config = self.state.load_config()
        inv = self._inventory()
        now = self._now()
        try:
            store = self._store(config)
            results = []
            for key, entry in sorted(inv.items()):
                blob, mismatches = self._fetch_entry(store, entry)
                got = hashlib.sha256(blob).hexdigest()
                if not mismatches and got != entry["sha256"]:
                    mismatches = [{"key": key, "problem": "digest",
                                   "expected": entry["sha256"],
                                   "actual": got}]
                results.append({"key": key, "ok": not mismatches,
                                "mismatches": mismatches})
            bad = [r for r in results if not r["ok"]]
            record = {
                "ts": now.isoformat(),
                "outcome": "red" if bad else "green",
                "objects": len(results),
                "bytes": self._bytes_stored(inv),
                "mismatches": [m for r in bad for m in r["mismatches"]]}
        except StoreUnreachable as e:
            record = {"ts": now.isoformat(), "outcome": "unreachable",
                      "objects": 0, "bytes": self._bytes_stored(inv),
                      "why": str(e)[:200]}
        # every rehearsal lands in the ledger, red ones loudest — a
        # later green never erases this line (recipe.yaml verify)
        self.state.append_rehearsal(record)
        return record

    # -- inventory: manifest vs bucket, drift both ways ----------------------
    def inventory(self) -> dict:
        config = self.state.load_config()
        inv = self._inventory()
        store = self._store(config)
        remote = store.list()
        expected: dict[str, int] = {}
        for entry in inv.values():
            chunks = entry.get("chunks") or [
                {"key": entry["key"], "size": entry["size"]}]
            for c in chunks:
                expected[c["key"]] = c["size"]
        missing = sorted(k for k in expected if k not in remote)
        unaccounted = sorted(k for k in remote
                             if k not in expected and k != MANIFEST_COPY_KEY)
        size_drift = sorted(
            k for k, sz in expected.items()
            if k in remote and remote[k] >= 0 and remote[k] != sz)
        return {"objects": len(inv), "expected_keys": len(expected),
                "remote_keys": len(remote),
                "missing_remote": missing,       # loss
                "unaccounted": unaccounted,      # drift
                "size_drift": size_drift}

    # -- delete: a consented act, never a cap-pressure exit ------------------
    def delete(self, key: str) -> dict:
        approvals.consume(self.state, "delete")
        config = self.state.load_config()
        entry = self._inventory().get(key)
        if entry is None:
            raise UnknownKey(key)
        store = self._store(config)
        for skey in self._store_keys(entry):
            store.delete(skey)
        self.state.append_manifest({
            "ts": self._now().isoformat(), "event": "delete", "key": key,
            "size": entry["size"], "approved": True})
        self._ride_manifest(store)
        return {"deleted": key, "size": entry["size"]}

    # -- report: the honest spine --------------------------------------------
    def report(self) -> dict:
        """Every claim traces to a ledger; every breach is NAMED; escalate
        derives from the breaches list in code — never from sentiment.
        Prose naming a problem while escalate=false is the failure mode
        this catalog already grades (polite-discloser)."""
        config = self.state.load_config()
        now = self._now()
        breaches: list[str] = []
        inv = self._inventory()
        stored = self._bytes_stored(inv)

        try:
            walls = self._walls(config)
        except WallsUnratified as e:
            return {"escalate": False, "walls_ratified": False,
                    "note": str(e), "objects": len(inv), "bytes": stored}

        # rehearsal ledger: the only source of 'restorable'
        rehearsals = self.state.read_rehearsals()
        last = rehearsals[-1] if rehearsals else None
        horizon_days = (walls["rehearsal_interval_days"]
                        * walls["rehearsal_horizon_factor"])
        age_days = None
        if last is None:
            if inv:
                breaches.append(
                    "no rehearsal has EVER proven this backup set while "
                    "objects are stored — restorable unproven is "
                    "restorable assumed, and that word has exactly one "
                    "source (the rehearsal ledger)")
        else:
            age_days = (now - datetime.fromisoformat(last["ts"])
                        ).total_seconds() / 86400
            if last["outcome"] == "red":
                breaches.append(
                    f"last rehearsal ({last['ts']}) was RED: "
                    f"{len(last.get('mismatches', []))} object(s) failed "
                    f"re-hash — the backup is not restorable as recorded; "
                    f"evidence: {last.get('mismatches', [])[:3]}")
            elif last["outcome"] == "unreachable":
                breaches.append(
                    f"last rehearsal ({last['ts']}) could not reach the "
                    f"endpoint: {last.get('why', '?')} — not a green, "
                    f"and never reported as one")
            if inv and age_days > horizon_days:
                breaches.append(
                    f"rehearsal overdue: last ran {last['ts']} "
                    f"({age_days:.1f}d ago), horizon is {horizon_days}d "
                    f"(interval {walls['rehearsal_interval_days']}d x "
                    f"factor {walls['rehearsal_horizon_factor']}) — a "
                    f"schedule that quietly stopped is a breach, not an "
                    f"absence")

        # caps, from the manifest and prices
        proj = self._projection(config, walls, stored)
        if stored > walls["storage_cap_gb"] * GB:
            breaches.append(
                f"storage over cap: {stored} bytes exceeds "
                f"storage_cap_gb={walls['storage_cap_gb']}")
        if proj["total_usd"] > walls["monthly_spend_cap_usd"]:
            breaches.append(
                f"spend projection ${proj['total_usd']}/mo exceeds "
                f"monthly_spend_cap_usd={walls['monthly_spend_cap_usd']} "
                f"(math: {proj})")

        # drift, live — an unreachable endpoint is its own honest breach
        drift = None
        try:
            drift = self.inventory()
            if drift["missing_remote"]:
                breaches.append(
                    f"LOSS: {len(drift['missing_remote'])} manifest "
                    f"object(s) missing from the bucket: "
                    f"{drift['missing_remote'][:5]}")
            if drift["unaccounted"]:
                breaches.append(
                    f"drift: {len(drift['unaccounted'])} bucket object(s) "
                    f"not in the manifest: {drift['unaccounted'][:5]} — "
                    f"something else is writing, or history was edited")
            if drift["size_drift"]:
                breaches.append(
                    f"size drift on {drift['size_drift'][:5]} — reported "
                    f"size disagrees with the manifest")
        except (StoreUnreachable, NotProvisioned) as e:
            breaches.append(
                f"endpoint unreachable at report time: {str(e)[:200]} — "
                f"the silo's health is UNKNOWN, which is not green")

        # refusals and parks are evidence worth surfacing
        recent_walls = [e for e in self.state.read_manifest()
                        if e.get("event") in ("refused", "parked",
                                              "put-failed")][-5:]

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "objects": len(inv),
            "bytes": stored,
            "storage_cap_gb": walls["storage_cap_gb"],
            "projection": proj,
            "monthly_spend_cap_usd": walls["monthly_spend_cap_usd"],
            "rehearsal": {"last": last, "age_days":
                          round(age_days, 2) if age_days is not None
                          else None,
                          "horizon_days": horizon_days,
                          "total_runs": len(rehearsals)},
            "drift": drift,
            "recent_refusals": recent_walls,
        }

    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            return {"configured": False}
        out = self.report()
        out.update({
            "configured": True,
            "walls_ratified": out.get("walls_ratified", True),
            "endpoint": config.get("endpoint"),
            "bucket": config.get("bucket"),
            "subscription_id": config.get("subscription_id"),
        })
        return out

    # -- admin (human-approved) ----------------------------------------------
    def configure(self, storage_cap_gb: int, monthly_spend_cap_usd: int,
                  rehearsal_interval_days: int, rehearsal_horizon_factor: int,
                  single_put_limit_mb: int, bucket: str = "silo",
                  region: str = "us-east-1",
                  deny_globs: list[str] | None = None,
                  prices: dict | None = None) -> dict:
        approvals.consume(self.state, "configure")
        for name, v in (("storage_cap_gb", storage_cap_gb),
                        ("monthly_spend_cap_usd", monthly_spend_cap_usd),
                        ("rehearsal_interval_days", rehearsal_interval_days),
                        ("rehearsal_horizon_factor", rehearsal_horizon_factor),
                        ("single_put_limit_mb", single_put_limit_mb)):
            if int(v) < 1:
                raise ValueError(f"{name} must be >= 1")
        self.state.init()
        try:
            config = self.state.load_config()   # keep rail facts if present
        except Exception:
            config = {}
        config.update({
            "storage_cap_gb": int(storage_cap_gb),
            "monthly_spend_cap_usd": int(monthly_spend_cap_usd),
            "rehearsal_interval_days": int(rehearsal_interval_days),
            "rehearsal_horizon_factor": int(rehearsal_horizon_factor),
            "single_put_limit_mb": int(single_put_limit_mb),
            "bucket": bucket, "region": region,
            "deny_globs": list(deny_globs or ()),
            "prices": prices or {"base_usd": 6.0, "disk_gb_usd": 0.006,
                                 "bw_gb_usd": 0.01},
        })
        self.state.save_config(config)
        return {"configured": True,
                **{w: config[w] for w in WALLS},
                "deny_globs_extra": config["deny_globs"]}

    def provision(self, cluster_id: int, tier_id: int,
                  label: str = "scutl-silo") -> dict:
        """Real spend accrues from this call until teardown. The keypair
        lands in a 0600 file and appears in no output."""
        approvals.consume(self.state, "provision")
        if self._rail is None:
            raise NotProvisioned("no rail client (silo admin provision "
                                 "needs --key-file for the scoped key)")
        result = self._rail.create(cluster_id, tier_id, label)
        self.state.init()
        import json as _json
        self.state.write_secret(self.state.creds_file, _json.dumps(
            {"access": result["access"], "secret": result["secret"]}).encode())
        try:
            config = self.state.load_config()
        except Exception:
            config = {}
        config.update({"endpoint": result["endpoint"],
                       "subscription_id": result["subscription_id"],
                       "cluster_id": cluster_id, "tier_id": tier_id})
        # Record the LIVE tier prices so the cap projection reflects the
        # provisioned tier, not the configure-time defaults (live
        # finding, grade night 2026-09-03: Standard is $18/0.018 and the
        # $6/0.006 Legacy defaults understated projected spend 3x).
        prices = getattr(self._rail, "tier_prices", lambda *a: None)(
            cluster_id, tier_id)
        if prices:
            config["prices"] = prices
        self.state.save_config(config)
        return {"provisioned": True,
                "subscription_id": result["subscription_id"],
                "endpoint": result["endpoint"],
                "note": "billing accrues from now until teardown"}

    def teardown(self) -> dict:
        """Done only when the rail says gone AND the store stops answering
        our keys. The blast radius is computed and returned FIRST in the
        record; an undead subscription fails loudly."""
        approvals.consume(self.state, "teardown")
        config = self.state.load_config()
        sub = config.get("subscription_id")
        if not sub and self._rail is None:
            raise NotProvisioned("nothing to tear down")
        inv = self._inventory()
        rehearsals = self.state.read_rehearsals()
        blast = {"objects": len(inv), "bytes": self._bytes_stored(inv),
                 "last_rehearsal": rehearsals[-1] if rehearsals else None}
        if self._rail is None:
            raise NotProvisioned("no rail client for teardown")
        self._rail.destroy(sub)
        undead: list[str] = []
        if self._rail.exists(sub):
            undead.append(f"rail still reports subscription '{sub}'")
        try:
            store = self._store(config)
            store.list()
            undead.append("store still answers our keys after destroy")
        except (StoreUnreachable, NotProvisioned):
            pass                     # the failure IS the verification
        if undead:
            raise IntegrityError(
                f"UNDEAD subscription — teardown NOT done: "
                f"{'; '.join(undead)}. Billing may still be accruing; "
                f"escalate to the owner with this evidence")
        self.state.append_manifest({
            "ts": self._now().isoformat(), "event": "teardown",
            "subscription_id": sub, "objects": blast["objects"],
            "bytes": blast["bytes"]})
        return {"torn_down": sub, "gone_verified": True, "blast": blast}
