"""beacon core: the guardrail component of recipe #12.

Manifest invariants enforced HERE, in code (recipe.yaml components.beacon):
  - 'up' has exactly two sources and requires both, fresh: prober
    evidence of the sentinel from outside within the deafness horizon,
    and a local probe ledger entry within its freshness window — a
    report calling a service up from registration, a stale prober
    label, an average, or local health alone is the graded
    green-washing sin
  - a prober's state label is never evidence; its evidence freshness
    is: last_observed_at older than max(factor x cadence, floor) — or
    a paused monitor — classifies prober-deaf, honest degraded, never
    up (the active witness fails GREEN; this recipe is built around
    that fact)
  - the watch itself is integrity-checked: a monitor whose config
    differs from the ledgered spec is watching the wrong door — a
    tamper breach with both quoted, never a silent re-upsert (a quiet
    fix launders the evidence that someone moved the watch)
  - splits are classified, never argued away: local-green/prober-red
    is unreachable (the headline case); prober-green/local-red is
    internal-down; neither vantage overrules the other
  - retiring a watch retires an obligation: deregistration (and the
    monitor pause/delete inside it) is approval-gated, prints its
    blast radius, and tombstones the ledger; a tombstoned target still
    probed is a zombie breach; a tombstoned target's monitor still
    standing outlived its tombstone
  - foreign evidence is an alarm: a monitor the ledger never
    registered is a custody breach, never adopted
  - beacon watches, never heals: no code path restarts, redeploys, or
    re-registers anything to green a report
  - escalation is STRUCTURAL: every breached wall appends a named
    breach and escalate=true derives from the list in code

Service output is data at full width: nothing a watched service
serves steers beacon's registration, classification, or escalation —
accounting derives from the ledgers and the evidence timestamps,
never from page content beyond the sentinel comparison itself.
"""

from __future__ import annotations

import hashlib
import json
import secrets as pysecrets
from datetime import datetime, timezone

from . import approvals
from .rails import (LocalRail, ProberRail, ProberUnreachable,
                    TargetInvalid)
from .state import StateDir, UnknownTarget

MIN_SENTINEL_LEN = 8
MIN_CADENCE_SECONDS = 60
MIN_LOCAL_CADENCE_SECONDS = 30

WALLS = ("max_targets", "prober_horizon_factor",
         "prober_horizon_floor_minutes", "local_freshness_factor",
         "verifier_horizon_factor")


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class WallsUnratified(Exception):
    """The five walls are not all set; run 'beacon admin configure' first."""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Manager:
    def __init__(self, state: StateDir | None = None,
                 local: LocalRail | None = None,
                 prober: ProberRail | None = None, now_fn=None):
        self.state = state or StateDir()
        self._local = local
        self._prober = prober
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- substrate access -------------------------------------------------
    @property
    def local(self) -> LocalRail:
        if self._local is None:
            from .live import LiveLocal
            self._local = LiveLocal(self.state)
        return self._local

    @property
    def prober(self) -> ProberRail:
        if self._prober is None:
            from .live import LiveProber
            self._prober = LiveProber(self.state)
        return self._prober

    def _walls(self, config: dict) -> dict:
        missing = [w for w in WALLS if w not in config]
        if missing:
            raise WallsUnratified(
                f"unratified: {', '.join(missing)} — all five walls are "
                f"owner-set before the first registration (recipe.yaml setup)")
        return {w: int(config[w]) for w in WALLS}

    # -- registration (idempotent; never a silent re-upsert over drift) ----
    @staticmethod
    def _spec_hash(target_id: str, url: str, sentinel: str,
                   cadence_seconds: int) -> str:
        blob = json.dumps({"target": target_id, "url": url,
                           "sentinel": sentinel,
                           "cadence_seconds": int(cadence_seconds)},
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    @staticmethod
    def _config_matches(target: dict, monitor: dict) -> bool:
        mc = monitor.get("config", {})
        return (mc.get("url") == target["url"]
                and mc.get("keyword") == target["sentinel"]
                and int(mc.get("cadence_seconds", -1))
                == int(target["cadence_seconds"]))

    def register(self, target_id: str, url: str, sentinel: str,
                 cadence_seconds: int,
                 local_cadence_seconds: int) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        target_id = target_id.strip().lower()
        if not target_id or "/" in target_id or " " in target_id:
            raise ValueError(f"invalid target id '{target_id}'")
        # the spec wall (manifest verify: nothing lands at the prober)
        if not str(url).startswith(("http://", "https://")):
            raise TargetInvalid(f"url '{url}' is not http(s)")
        if not sentinel or len(sentinel) < MIN_SENTINEL_LEN:
            raise TargetInvalid(
                f"sentinel must be at least {MIN_SENTINEL_LEN} chars — a "
                f"string a parking page could contain is not an identity "
                f"wall (manifest: content-wall)")
        if int(cadence_seconds) < MIN_CADENCE_SECONDS:
            raise TargetInvalid(
                f"cadence_seconds {cadence_seconds} < {MIN_CADENCE_SECONDS}")
        if int(local_cadence_seconds) < MIN_LOCAL_CADENCE_SECONDS:
            raise TargetInvalid(
                f"local_cadence_seconds {local_cadence_seconds} < "
                f"{MIN_LOCAL_CADENCE_SECONDS}")
        cadence_seconds = int(cadence_seconds)
        local_cadence_seconds = int(local_cadence_seconds)
        spec_hash = self._spec_hash(target_id, url, sentinel,
                                    cadence_seconds)

        existing = None
        if self.state.target_file(target_id).exists():
            existing = self.state.load_target(target_id)

        if (existing and not existing.get("tombstoned")
                and existing["spec_hash"] == spec_hash):
            # convergence candidate — but NEVER a silent re-upsert over
            # drift: read what the prober actually watches first
            monitors = {m["monitor_id"]: m for m in self.prober.read_all()}
            monitor = monitors.get(existing.get("monitor_id"))
            if monitor is not None:
                if not self._config_matches(existing, monitor):
                    raise LimitRefused(
                        f"monitor for '{target_id}' has DRIFTED from the "
                        f"ledgered spec — watching the wrong door: ledger "
                        f"{{url: {existing['url']}, keyword: "
                        f"{existing['sentinel']}, cadence: "
                        f"{existing['cadence_seconds']}}} vs monitor "
                        f"{json.dumps(monitor.get('config', {}), sort_keys=True)}; "
                        f"re-registering over drift would launder the "
                        f"tamper evidence — run verify, then decide with "
                        f"the owner")
                return {"converged": True, "target": target_id}
            # ledgered target whose monitor vanished: recreating is
            # legitimate repair, but the vanishing is EVIDENCE — ledgered
            up = self.prober.upsert(target_id, url, sentinel,
                                    cadence_seconds)
            existing["monitor_id"] = up["monitor_id"]
            self.state.save_target(target_id, existing)
            self.state.append_probe({
                "ts": _iso(self._now()), "event": "register",
                "target": target_id, "spec_hash": spec_hash,
                "recreated": True,
                "note": "monitor was missing at the prober; recreated — "
                        "the gap in outside coverage is real and stays "
                        "in this ledger"})
            return {"converged": True, "target": target_id,
                    "recreated": True}

        is_new_slot = existing is None or existing.get("tombstoned")
        if is_new_slot:
            active = self.state.target_ids()
            if len(active) >= walls["max_targets"]:
                # LOUD: a service the operator believes is watched but
                # silently is not would be beacon's own orphan shape
                raise LimitRefused(
                    f"{len(active)} target(s) registered, max_targets is "
                    f"{walls['max_targets']} — this registration is "
                    f"REFUSED and '{target_id}' is NOT watched; the fix "
                    f"is owner-decided (raise the wall or retire a "
                    f"target), never a silent 41st monitor")

        # prober first: an obligation to watch must not begin life
        # unwatchable — ProberUnreachable propagates as refusal.
        # A changed spec UPDATES the monitor in place (same name):
        # deleting and recreating would reset the prober-side incident
        # history (manifest: history-reset).
        up = self.prober.upsert(target_id, url, sentinel, cadence_seconds)
        record = {
            "target_id": target_id, "url": url, "sentinel": sentinel,
            "cadence_seconds": cadence_seconds,
            "local_cadence_seconds": local_cadence_seconds,
            "spec_hash": spec_hash, "monitor_id": up.get("monitor_id"),
            "registered": _iso(self._now()),
        }
        self.state.save_target(target_id, record)
        self.state.append_probe({
            "ts": _iso(self._now()), "event": "register",
            "target": target_id, "url": url,
            "cadence_seconds": cadence_seconds,
            "local_cadence_seconds": local_cadence_seconds,
            "spec_hash": spec_hash,
            **({"replaces": existing["spec_hash"]} if existing else {})})
        return {"registered": target_id, "spec_hash": spec_hash,
                "monitor_created": bool(up.get("created")),
                "changed": existing is not None}

    # -- the local prover (rides bell; local truth only) --------------------
    def _ledger_oids(self) -> set[str]:
        return {e["oid"] for e in self.state.read_probes()
                if e.get("event") == "probe" and e.get("oid")}

    def probe(self, target_id: str, oid: str | None = None) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        target = self.state.load_target(target_id)
        now = self._now()
        if target.get("tombstoned"):
            # the zombie shape: record the evidence, then refuse — a
            # tombstoned target's silence is rest, its PROBING is not
            self.state.append_probe({
                "ts": _iso(now), "event": "zombie-probe",
                "target": target_id})
            raise LimitRefused(
                f"target '{target_id}' was deregistered "
                f"({target['tombstoned']}) but something still probes it "
                f"— zombie probe; refusing and leaving the evidence in "
                f"the ledger")
        if oid is not None:
            if oid in self._ledger_oids():
                raise LimitRefused(
                    f"oid '{oid}' is already in the probe ledger — "
                    f"observations are exactly-once; the window counts "
                    f"it once")
        else:
            oid = pysecrets.token_hex(8)

        obs = self.local.fetch(target)
        status = obs.get("status_code")
        sentinel_present = bool(obs.get("sentinel_present"))
        serial_age = obs.get("serial_age_seconds")
        fresh_window = (walls["local_freshness_factor"]
                        * target["local_cadence_seconds"])
        serial_fresh = serial_age is not None and serial_age <= fresh_window
        ok = (status is not None and 200 <= int(status) < 300
              and sentinel_present and serial_fresh)
        # the ledger commit: an observation is an observation whether
        # the service answered or not — failed fetches are recorded,
        # never retried into silence
        self.state.append_probe({
            "ts": _iso(now), "event": "probe", "target": target_id,
            "oid": oid, "ok": ok, "status_code": status,
            "sentinel_present": sentinel_present,
            "serial_age_seconds": serial_age,
            "serial_fresh": serial_fresh})
        return {"probed": target_id, "oid": oid, "ok": ok,
                "status_code": status,
                "sentinel_present": sentinel_present,
                "serial_fresh": serial_fresh}

    # -- reconciliation (the meaning of 'is it up') --------------------------
    def _latest_probe(self, target_id: str) -> dict | None:
        for e in reversed(self.state.read_probes()):
            if e.get("event") == "probe" and e.get("target") == target_id:
                return e
        return None

    def _reconcile(self, config: dict, walls: dict, now: datetime) -> dict:
        """Shared classification: verify() appends its outcome to the
        verify ledger; report() presents it with tails. Both read the
        prober ONCE (the live rail is rate-limited)."""
        breaches: list[str] = []
        rows: list[dict] = []
        counts: dict[str, int] = {}
        prober_dark = False
        monitors: dict[str, dict] = {}
        try:
            monitors = {m["monitor_id"]: m for m in self.prober.read_all()}
        except ProberUnreachable:
            prober_dark = True
            breaches.append(
                "prober dark: the outside failure domain is unreachable "
                "— every target below is classified prober-deaf and this "
                "report's coverage is DEGRADED (local ledger only); "
                "local-only truth is never presented at full confidence")

        floor_s = walls["prober_horizon_floor_minutes"] * 60

        referenced: set[str] = set()
        for tid in self.state.target_ids(include_tombstoned=True):
            t = self.state.load_target(tid)
            if t.get("monitor_id"):
                referenced.add(t["monitor_id"])
            if t.get("tombstoned"):
                if not prober_dark and t.get("monitor_id") in monitors:
                    breaches.append(
                        f"zombie watch: target '{tid}' was deregistered "
                        f"({t['tombstoned']}) but its monitor still "
                        f"stands at the prober — the watch outlived its "
                        f"tombstone")
                continue

        for tid in self.state.target_ids():
            t = self.state.load_target(tid)
            horizon = max(walls["prober_horizon_factor"]
                          * t["cadence_seconds"], floor_s)

            # inside face: the probe ledger, with its own freshness
            last = self._latest_probe(tid)
            local_fresh = False
            local_ok = False
            local_age = None
            if last is not None:
                local_age = int((now - _parse(last["ts"])).total_seconds())
                local_fresh = local_age <= (walls["local_freshness_factor"]
                                            * t["local_cadence_seconds"])
                local_ok = bool(last.get("ok")) and local_fresh
            if not local_fresh:
                breaches.append(
                    f"stale local ledger: target '{tid}' has "
                    f"{'no probe entry' if last is None else f'a newest probe {local_age}s old'}"
                    f" — the inside half of 'up' expires too; a fresh "
                    f"prober plus a stale local ledger is degraded, not "
                    f"corroborated")

            # outside face: evidence freshness first, label second
            monitor = None if prober_dark else monitors.get(
                t.get("monitor_id"))
            prober_evidence = None
            if prober_dark:
                cls = "prober-deaf"
            elif monitor is None:
                cls = "unwatched"
                breaches.append(
                    f"unwatched target: '{tid}' is ledgered but no "
                    f"monitor stands at the prober — the operator "
                    f"believes it is watched and it is not")
            elif not self._config_matches(t, monitor):
                cls = "unwatched"
                breaches.append(
                    f"watching the wrong door: monitor for '{tid}' has "
                    f"drifted from the ledgered spec — ledger {{url: "
                    f"{t['url']}, keyword: {t['sentinel']}, cadence: "
                    f"{t['cadence_seconds']}}} vs monitor "
                    f"{json.dumps(monitor.get('config', {}), sort_keys=True)}"
                    f" — integrity breach; beacon does not silently "
                    f"re-upsert history")
            else:
                age = None
                if monitor.get("last_observed_at"):
                    age = int((now - _parse(monitor["last_observed_at"]))
                              .total_seconds())
                prober_evidence = {
                    "state": monitor.get("state"),
                    "last_observed_at": monitor.get("last_observed_at"),
                    "evidence_age_seconds": age,
                    "paused": bool(monitor.get("paused")),
                    "incidents": monitor.get("incidents", [])}
                if monitor.get("paused"):
                    cls = "prober-deaf"
                    breaches.append(
                        f"paused monitor: '{tid}' is paused at the "
                        f"prober — a paused watch generates no evidence "
                        f"and its last word is not current; if nobody "
                        f"approved this, someone silenced an alarm")
                elif age is None or age > horizon:
                    cls = "prober-deaf"
                    breaches.append(
                        f"prober-deaf: evidence for '{tid}' is "
                        f"{'absent' if age is None else f'{age}s old'} "
                        f"(horizon {horizon}s) — the state label "
                        f"'{monitor.get('state')}' is not evidence; a "
                        f"stale green label is a woodpile, not a beacon")
                else:
                    prober_up = monitor.get("state") == "up"
                    if local_ok and prober_up:
                        cls = "up-corroborated"
                    elif local_ok and not prober_up:
                        cls = "unreachable"
                        breaches.append(
                            f"unreachable: '{tid}' answers locally but "
                            f"the prober sees it down — the process "
                            f"lives, customers cannot reach it; local "
                            f"health does not argue this away")
                    elif local_fresh and prober_up:
                        cls = "internal-down"
                        breaches.append(
                            f"internal-down: the prober sees the "
                            f"sentinel for '{tid}' but the local probe "
                            f"fails — neither vantage overrules the "
                            f"other")
                    elif not prober_up:
                        cls = "down-confirmed"
                        breaches.append(
                            f"down-confirmed: '{tid}' is down from "
                            f"{'both vantage points' if local_fresh else 'outside (and the local ledger is stale)'}")
                    else:
                        cls = "prober-only"  # outside green, inside stale
            counts[cls] = counts.get(cls, 0) + 1
            rows.append({
                "target": tid, "class": cls,
                "prober": prober_evidence,
                "local": None if last is None else {
                    "ok": bool(last.get("ok")),
                    "ts": last["ts"], "age_seconds": local_age,
                    "fresh": local_fresh},
            })

        if not prober_dark:
            foreign = [mid for mid in monitors if mid not in referenced]
            for mid in sorted(foreign):
                breaches.append(
                    f"foreign monitor: '{monitors[mid].get('name', mid)}' "
                    f"({mid}) stands in the prober account but the "
                    f"ledger never registered it — leaked key or shared "
                    f"custody; an alarm, never an adoption")

        return {"rows": rows, "counts": counts, "breaches": breaches,
                "prober_dark": prober_dark,
                "coverage": ("prober-dark" if prober_dark else
                             "degraded" if counts.get("prober-deaf") or
                             counts.get("unwatched") else "full")}

    def verify(self) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        now = self._now()
        verifies = self.state.read_verifies()
        last_verify = verifies[-1]["ts"] if verifies else None
        breaches: list[str] = []

        # a reconciliation that arrives late is itself evidence: running
        # now does not erase that the watcher was silent past its own
        # horizon (bell's design find, inherited whole — the gap lands
        # in THIS ledger line, or verify HEALS the deafness it should
        # be reporting)
        vcad = int(config.get("verify_cadence_seconds", 0))
        if last_verify and vcad:
            horizon = vcad * walls["verifier_horizon_factor"]
            gap = int((now - _parse(last_verify)).total_seconds())
            if gap > horizon:
                breaches.append(
                    f"deaf verifier (late reconciliation): this verify "
                    f"arrives {gap}s after the previous one (horizon "
                    f"{horizon}s) — the gap is a breach even though the "
                    f"verify has now run")

        rec = self._reconcile(config, walls, now)
        breaches.extend(rec["breaches"])
        entry = {"ts": _iso(now), "since": last_verify,
                 "counts": rec["counts"], "breaches": breaches,
                 "prober_dark": rec["prober_dark"],
                 "coverage": rec["coverage"]}
        self.state.append_verify(entry)
        return {"escalate": bool(breaches), "breaches": breaches,
                "counts": rec["counts"], "classification": rec["rows"],
                "coverage": rec["coverage"],
                "prober_dark": rec["prober_dark"]}

    # -- report: the spine. Current state first, averages decorate ----------
    def report(self) -> dict:
        """Current classification LEADS; incidents are quoted verbatim
        inside each row; uptime percentages are decoration placed
        beside — never instead of — current state; coverage is labeled;
        escalate derives from the breaches list in code. 'The monitor
        exists' is never the answer to 'can customers reach it now'
        (green-washing, the graded sin)."""
        config = self.state.load_config()
        walls = self._walls(config)
        now = self._now()
        rec = self._reconcile(config, walls, now)
        breaches = list(rec["breaches"])

        # decoration only: the percentage sits inside the row, after
        # the class it may never replace
        probes = self.state.read_probes()
        for row in rec["rows"]:
            obs = [e for e in probes if e.get("event") == "probe"
                   and e.get("target") == row["target"]]
            row["local_ok_percent_decoration"] = (
                round(100 * sum(1 for e in obs if e.get("ok")) / len(obs), 1)
                if obs else None)

        # the deafness doctrine, pointed at the watcher itself
        verifies = self.state.read_verifies()
        last_verify = verifies[-1]["ts"] if verifies else None
        verifier_age_s = None
        vcad = int(config.get("verify_cadence_seconds", 0))
        active = self.state.target_ids()
        if active:
            if last_verify is None:
                breaches.append(
                    "deaf verifier: no reconciliation has EVER run while "
                    "targets are registered — watching unproven is "
                    "watching assumed")
            elif vcad:
                verifier_age_s = int((now - _parse(last_verify))
                                     .total_seconds())
                horizon = vcad * walls["verifier_horizon_factor"]
                if verifier_age_s > horizon:
                    breaches.append(
                        f"deaf verifier: last reconciliation {last_verify} "
                        f"({verifier_age_s}s ago) exceeds its own horizon "
                        f"({horizon}s = cadence x "
                        f"{walls['verifier_horizon_factor']}) — the "
                        f"watcher has no exemption from the deafness "
                        f"doctrine, and bell's slot accounting is the "
                        f"second wall behind this one")
            else:
                breaches.append(
                    "verifier unscheduled: targets are watched but "
                    "verify_cadence_seconds is 0 — bc_verify is not on "
                    "any bell; silence about silence")

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "classification": rec["rows"],   # current state FIRST
            "counts": rec["counts"],
            "coverage": rec["coverage"],
            "prober_dark": rec["prober_dark"],
            "target_count": {"active": len(active),
                             "max_targets": walls["max_targets"]},
            "verifier": {"last_verify": last_verify,
                         "age_seconds": verifier_age_s},
            # tails quoted verbatim (every claim traces to a ledger)
            "probe_tail": probes[-10:],
            "verify_tail": verifies[-3:],
        }

    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            return {"configured": False}
        out = self.report()
        out.update({
            "configured": True,
            "walls": {k: config[k] for k in WALLS},
            "verify_cadence_seconds": config.get("verify_cadence_seconds"),
            "prober_api_base": config.get("prober_api_base"),
        })
        return out

    # -- admin (human-approved) --------------------------------------------
    def deregister(self, target_id: str) -> dict:
        approvals.consume(self.state, "deregister")
        target = self.state.load_target(target_id)
        if target.get("tombstoned"):
            raise ValueError(f"target '{target_id}' is already tombstoned "
                             f"({target['tombstoned']})")
        obs = [e for e in self.state.read_probes()
               if e.get("event") == "probe" and e.get("target") == target_id]
        monitor_detached = True
        try:
            if target.get("monitor_id"):
                self.prober.pause(target["monitor_id"])
                self.prober.delete(target["monitor_id"])
        except ProberUnreachable:
            monitor_detached = False
        now = _iso(self._now())
        self.state.append_probe({
            "ts": now, "event": "deregister", "target": target_id,
            "monitor_detached": monitor_detached})
        self.state.save_target(target_id, {**target, "tombstoned": now})
        # blast radius, printed AFTER the act records what it was
        return {"deregistered": target_id,
                "registered_since": target["registered"],
                "lifetime_probes": len(obs),
                "last_probed": obs[-1]["ts"] if obs else None,
                "monitor_detached": monitor_detached}

    def configure(self, max_targets: int, prober_horizon_factor: int,
                  prober_horizon_floor_minutes: int,
                  local_freshness_factor: int,
                  verifier_horizon_factor: int,
                  verify_cadence_seconds: int,
                  prober_api_base: str) -> dict:
        approvals.consume(self.state, "configure")
        for name, v in (("max_targets", max_targets),
                        ("prober_horizon_factor", prober_horizon_factor),
                        ("prober_horizon_floor_minutes",
                         prober_horizon_floor_minutes),
                        ("local_freshness_factor", local_freshness_factor),
                        ("verifier_horizon_factor", verifier_horizon_factor),
                        ("verify_cadence_seconds", verify_cadence_seconds)):
            if int(v) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not str(prober_api_base).startswith(("http://", "https://")):
            raise ValueError("prober_api_base must be http(s):// (the base "
                             "URL is a parameter, never a constant)")
        self.state.init()
        config = {
            "max_targets": int(max_targets),
            "prober_horizon_factor": int(prober_horizon_factor),
            "prober_horizon_floor_minutes": int(prober_horizon_floor_minutes),
            "local_freshness_factor": int(local_freshness_factor),
            "verifier_horizon_factor": int(verifier_horizon_factor),
            "verify_cadence_seconds": int(verify_cadence_seconds),
            "prober_api_base": prober_api_base.rstrip("/"),
        }
        self.state.save_config(config)
        return {"configured": True, **config}
