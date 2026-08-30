"""bell core: the guardrail component of recipe #11.

Manifest invariants enforced HERE, in code (recipe.yaml components.bell):
  - the word 'ran' has exactly one source: the firing ledger; the
    witness corroborates by rid and never testifies alone — a report
    calling a schedule healthy from its registration is the graded
    green-washing sin
  - firings are exactly-once, in-order, append-only: duplicate rids
    refuse, entries are never edited, and a unit file that differs
    from the ledgered spec is an integrity breach (tamper), never a
    silent re-render
  - silence escalates from both directions: missed slots breach with
    the slot named, and the verifier itself past its own horizon is a
    breach — the deafness doctrine has no exemption for the watcher
  - degradation is honest: witness-unreachable records unwitnessed
    (never a job failure, never silently fine); catch-up runs are
    labeled catchup, never laundered as on-time
  - removing a schedule removes an obligation: deregistration is
    approval-gated, prints its blast radius, and tombstones the
    ledger; a tombstoned job that still fires is a zombie breach
  - foreign evidence is an alarm: a witness rid the ledger never
    issued is a custody breach, never a success
  - escalation is STRUCTURAL: every breached wall appends a named
    breach and escalate=true derives from the list in code

Job output is data at full width: nothing a job prints steers bell's
scheduling, registration, or escalation — accounting derives from the
ledgers, never from job content.
"""

from __future__ import annotations

import hashlib
import json
import secrets as pysecrets
from datetime import datetime, timezone

from . import approvals
from .rails import (InvalidSchedule, SystemdRail, WitnessRail,
                    WitnessUnreachable)
from .state import StateDir, UnknownJob

VERIFIER_JOB = "_verify"
GRACE_FLOOR_SECONDS = 60

WALLS = ("max_jobs", "grace_divisor", "grace_cap_minutes",
         "verifier_horizon_factor", "unwitnessed_streak_threshold")


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class WallsUnratified(Exception):
    """The five walls are not all set; run 'bell admin configure' first."""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Manager:
    def __init__(self, state: StateDir | None = None,
                 systemd: SystemdRail | None = None,
                 witness: WitnessRail | None = None, now_fn=None):
        self.state = state or StateDir()
        self._systemd = systemd
        self._witness = witness
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- substrate access -------------------------------------------------
    @property
    def systemd(self) -> SystemdRail:
        if self._systemd is None:
            from .live import LiveSystemd
            self._systemd = LiveSystemd(self.state)
        return self._systemd

    @property
    def witness(self) -> WitnessRail:
        if self._witness is None:
            from .live import LiveWitness
            self._witness = LiveWitness(self.state)
        return self._witness

    def _walls(self, config: dict) -> dict:
        missing = [w for w in WALLS if w not in config]
        if missing:
            raise WallsUnratified(
                f"unratified: {', '.join(missing)} — all five walls are "
                f"owner-set before the first registration (recipe.yaml setup)")
        return {w: int(config[w]) for w in WALLS}

    # -- registration (idempotent end to end) ------------------------------
    @staticmethod
    def _spec_hash(job_id: str, argv: list[str], normalized: str) -> str:
        blob = json.dumps({"job": job_id, "argv": argv,
                           "schedule": normalized}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def register(self, job_id: str, argv: list[str], schedule: str,
                 internal: bool = False) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        job_id = job_id.strip().lower()
        if not job_id or "/" in job_id or " " in job_id:
            raise ValueError(f"invalid job id '{job_id}'")
        if job_id.startswith("_") and not internal:
            raise ValueError("job ids starting with '_' are internal")
        if not argv:
            raise ValueError("argv must be non-empty")

        # the parse wall: normalize or refuse; UTC only (dst-drift is
        # refused at the wall, never accepted-and-skewed)
        parsed = self.systemd.calendar_parse(schedule)
        if parsed.get("tz"):
            raise InvalidSchedule(
                f"schedule carries timezone '{parsed['tz']}' — bell "
                f"schedules are UTC only (manifest: dst-drift)")
        normalized = parsed["normalized"]
        cadence = int(self.systemd.cadence_seconds(normalized))
        grace = max(GRACE_FLOOR_SECONDS,
                    min(cadence // walls["grace_divisor"],
                        walls["grace_cap_minutes"] * 60))
        spec_hash = self._spec_hash(job_id, list(argv), normalized)

        existing = None
        if self.state.job_file(job_id).exists():
            existing = self.state.load_job(job_id)
        if (existing and not existing.get("tombstoned")
                and existing["spec_hash"] == spec_hash):
            # convergence: same spec twice is one job — re-assert the
            # substrate (both idempotent) and append nothing
            up = self.witness.upsert(job_id, normalized, grace)
            self.systemd.render_units({**existing})
            return {"converged": True, "job": job_id,
                    "witness_created": bool(up.get("created"))}

        is_new_slot = existing is None or existing.get("tombstoned")
        if (is_new_slot and not job_id.startswith("_")):
            active = self.state.job_ids()
            if len(active) >= walls["max_jobs"]:
                # LOUD: a job the operator believes is registered but
                # silently is not would be bell's own orphan shape
                raise LimitRefused(
                    f"{len(active)} job(s) registered, max_jobs is "
                    f"{walls['max_jobs']} — this registration is REFUSED "
                    f"and '{job_id}' is NOT on the bell; the fix is "
                    f"owner-decided (raise the wall or retire a job), "
                    f"never a silent 21st check")

        # witness first: an obligation must not begin life
        # uncorroboratable — WitnessUnreachable propagates as refusal
        up = self.witness.upsert(job_id, normalized, grace)
        record = {
            "job_id": job_id, "argv": list(argv), "schedule": normalized,
            "cadence_seconds": cadence, "grace_seconds": grace,
            "spec_hash": spec_hash, "witness_uuid": up.get("uuid"),
            "registered": _iso(self._now()),
            "internal": bool(internal),
        }
        self.systemd.render_units(record)
        self.state.save_job(job_id, record)
        self.state.append_firing({
            "ts": _iso(self._now()), "event": "register", "job": job_id,
            "schedule": normalized, "cadence_seconds": cadence,
            "grace_seconds": grace, "spec_hash": spec_hash,
            **({"replaces": existing["spec_hash"]} if existing else {})})
        return {"registered": job_id, "schedule": normalized,
                "cadence_seconds": cadence, "grace_seconds": grace,
                "witness_created": bool(up.get("created")),
                "changed": existing is not None}

    def register_verifier(self, schedule: str) -> dict:
        """The verifier goes on its own bell (recipe.yaml setup): the
        deafness doctrine has no exemption for the watcher."""
        return self.register(VERIFIER_JOB, ["bell", "verify"], schedule,
                             internal=True)

    # -- firing (the run harness every unit invokes) -----------------------
    def _ledger_rids(self, job_id: str | None = None) -> set[str]:
        return {e["rid"] for e in self.state.read_firings()
                if e.get("event") == "fire"
                and (job_id is None or e.get("job") == job_id)}

    def fire(self, job_id: str, rid: str | None = None) -> dict:
        config = self.state.load_config()
        self._walls(config)
        job = self.state.load_job(job_id)
        now = self._now()
        if job.get("tombstoned"):
            # the zombie shape: record the evidence, then refuse — a
            # tombstoned obligation's silence is rest, its FIRING is not
            self.state.append_firing({
                "ts": _iso(now), "event": "zombie-fire", "job": job_id})
            raise LimitRefused(
                f"job '{job_id}' was deregistered "
                f"({job['tombstoned']}) but something still fires it — "
                f"zombie timer; refusing to run and leaving the evidence "
                f"in the ledger")
        if rid is not None:
            if rid in self._ledger_rids():
                raise LimitRefused(
                    f"rid '{rid}' is already in the firing ledger — "
                    f"firings are exactly-once; the slot counts once")
        else:
            rid = pysecrets.token_hex(8)

        slot, late = self.systemd.slot_for(job["schedule"], now)
        kind = "on-time"
        if (late > job["grace_seconds"]
                and _parse(slot) >= _parse(job["registered"])):
            kind = "catchup"  # repairs the slot; never impersonates it

        try:
            start_ok = self.witness.ping(job_id, "start", rid)
        except WitnessUnreachable:
            start_ok = False
        result = self.systemd.run_argv(job, rid)
        exit_status = int(result["exit_status"])
        # the ledger commit: after the run, before (and regardless of)
        # the closing ping — 'ran' never depends on witness reachability
        self.state.append_firing({
            "ts": _iso(self._now()), "event": "fire", "job": job_id,
            "rid": rid, "slot": slot, "late_seconds": int(late),
            "kind": kind, "exit_status": exit_status,
            "duration_ms": int(result.get("duration_ms", 0)),
            "witnessed_start": bool(start_ok)})
        end_kind = "success" if exit_status == 0 else str(exit_status)
        try:
            end_ok = self.witness.ping(job_id, end_kind, rid)
        except WitnessUnreachable:
            end_ok = False
        witnessed = bool(start_ok and end_ok)
        self.state.append_firing({
            "ts": _iso(self._now()), "event": "fire-witness", "job": job_id,
            "rid": rid, "ok": witnessed})
        return {"fired": job_id, "rid": rid, "slot": slot, "kind": kind,
                "exit_status": exit_status, "witnessed": witnessed}

    # -- reconciliation (the meaning of 'verify firing') -------------------
    def _witnessed_rids(self) -> set[str]:
        return {e["rid"] for e in self.state.read_firings()
                if e.get("event") == "fire-witness" and e.get("ok")}

    def _unwitnessed_streak(self, job_id: str) -> int:
        witnessed = self._witnessed_rids()
        streak = 0
        for e in reversed(self.state.read_firings()):
            if e.get("event") != "fire" or e.get("job") != job_id:
                continue
            if e["rid"] in witnessed:
                break
            streak += 1
        return streak

    def verify(self) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        now = self._now()
        verifies = self.state.read_verifies()
        last_verify = verifies[-1]["ts"] if verifies else None
        firings = self.state.read_firings()
        breaches: list[str] = []
        accounting: dict[str, list[dict]] = {}
        counts = {"fired-and-witnessed": 0, "fired-unwitnessed": 0,
                  "catchup": 0, "missed": 0, "pending": 0}

        # a reconciliation that arrives late is itself evidence: running
        # now does not erase that the watcher was silent past its own
        # horizon (deafness doctrine — the gap lands in THIS ledger line)
        if last_verify and self.state.job_file(VERIFIER_JOB).exists():
            vjob = self.state.load_job(VERIFIER_JOB)
            if not vjob.get("tombstoned"):
                horizon = (vjob["cadence_seconds"]
                           * walls["verifier_horizon_factor"])
                gap = int((now - _parse(last_verify)).total_seconds())
                if gap > horizon:
                    breaches.append(
                        f"deaf verifier (late reconciliation): this "
                        f"verify arrives {gap}s after the previous one "
                        f"(horizon {horizon}s) — the gap is a breach "
                        f"even though the verify has now run")
        witnessed = self._witnessed_rids()
        witness_dark = False

        all_ids = self.state.job_ids(include_internal=True,
                                     include_tombstoned=True)
        for jid in all_ids:
            job = self.state.load_job(jid)
            fires = [e for e in firings
                     if e.get("event") == "fire" and e.get("job") == jid]
            ledger_rids = {e["rid"] for e in fires}

            if job.get("tombstoned"):
                dead_since = _parse(job["tombstoned"])
                walkers = [e for e in firings if e.get("job") == jid
                           and e.get("event") in ("fire", "zombie-fire")
                           and _parse(e["ts"]) > dead_since]
                if walkers:
                    breaches.append(
                        f"zombie job '{jid}': {len(walkers)} firing "
                        f"event(s) after its tombstone "
                        f"({job['tombstoned']}) — deregistration removed "
                        f"the obligation but not the timer")
                continue

            t0 = job["registered"]
            if last_verify and _parse(last_verify) > _parse(t0):
                t0 = last_verify
            slots = self.systemd.slots_between(job["schedule"],
                                               _parse(t0), now)
            by_slot: dict[str, dict] = {}
            for e in fires:
                by_slot[e["slot"]] = e  # latest entry per slot wins
            rows = []
            for slot in slots:
                e = by_slot.get(slot)
                if e is None:
                    due = _parse(slot).timestamp() + job["grace_seconds"]
                    if now.timestamp() < due:
                        cls = "pending"
                    else:
                        cls = "missed"
                        breaches.append(
                            f"missed slot: job '{jid}' slot {slot} has no "
                            f"firing ledger entry and its grace "
                            f"({job['grace_seconds']}s) has expired — "
                            f"nothing woke it")
                elif e["kind"] == "catchup":
                    cls = "catchup"
                elif e["rid"] in witnessed:
                    cls = "fired-and-witnessed"
                else:
                    cls = "fired-unwitnessed"
                counts[cls] += 1
                rows.append({"slot": slot, "class": cls})
            accounting[jid] = rows

            streak = self._unwitnessed_streak(jid)
            if streak >= walls["unwitnessed_streak_threshold"]:
                breaches.append(
                    f"unwitnessed streak: job '{jid}' has {streak} "
                    f"consecutive firings without witness corroboration "
                    f"(threshold {walls['unwitnessed_streak_threshold']}) "
                    f"— the second failure domain has been dark too long "
                    f"to trust the accounting")

            # the witness as corroboration — and as custody tripwire
            try:
                seen = self.witness.read(jid)
            except WitnessUnreachable:
                witness_dark = True
                continue
            foreign = [p["rid"] for p in seen.get("pings", [])
                       if p.get("rid") and p["rid"] not in ledger_rids]
            if foreign:
                breaches.append(
                    f"foreign ping: witness for '{jid}' shows rid(s) "
                    f"{sorted(set(foreign))} the ledger never issued — "
                    f"someone else can reach our check; custody alarm, "
                    f"not a success")

        entry = {"ts": _iso(now), "since": last_verify,
                 "counts": counts, "breaches": breaches,
                 "witness_dark": witness_dark}
        self.state.append_verify(entry)
        return {"escalate": bool(breaches), "breaches": breaches,
                "counts": counts, "accounting": accounting,
                "witness_dark": witness_dark}

    # -- report: the spine. Ledger-derived facts, honest escalation --------
    def report(self) -> dict:
        """Every wall checked from the ledgers, the unit files, and the
        witness, every breach NAMED, and escalate set from the breaches
        — never from sentiment. 'The timer exists' is never the answer
        to 'did it run' (green-washing, the graded sin)."""
        config = self.state.load_config()
        walls = self._walls(config)
        now = self._now()
        breaches: list[str] = []
        witness_dark = False

        active = self.state.job_ids(include_internal=True)
        external = self.state.job_ids()
        jobs_report = []
        for jid in active:
            job = self.state.load_job(jid)
            unit = self.systemd.read_unit(jid)
            if unit is None:
                breaches.append(
                    f"unit missing: job '{jid}' is ledgered but no timer "
                    f"exists on disk — nothing will fire it")
            elif unit.get("spec_hash") != job["spec_hash"]:
                breaches.append(
                    f"schedule tamper: job '{jid}' unit on disk "
                    f"({str(unit.get('spec_hash'))[:12]}) differs from the "
                    f"ledgered spec ({job['spec_hash'][:12]}) — integrity "
                    f"breach; bell does not silently re-render history")
            check = None
            try:
                check = self.witness.read(jid)
                if check.get("status") == "absent":
                    breaches.append(
                        f"witness check absent: job '{jid}' has no second "
                        f"failure domain — its silence would be invisible")
            except WitnessUnreachable:
                witness_dark = True
            streak = self._unwitnessed_streak(jid)
            if streak >= walls["unwitnessed_streak_threshold"]:
                breaches.append(
                    f"unwitnessed streak: job '{jid}' at {streak} "
                    f"(threshold {walls['unwitnessed_streak_threshold']})")
            fires = [e for e in self.state.read_firings()
                     if e.get("event") == "fire" and e.get("job") == jid]
            jobs_report.append({
                "job": jid, "schedule": job["schedule"],
                "grace_seconds": job["grace_seconds"],
                "firings": len(fires),
                "last_fired": fires[-1]["ts"] if fires else None,
                "unwitnessed_streak": streak,
                "witness_status": (check or {}).get("status")})

        # zombies: tombstoned jobs whose units are still on disk
        for jid in self.state.job_ids(include_internal=True,
                                      include_tombstoned=True):
            job = self.state.load_job(jid)
            if job.get("tombstoned") and self.systemd.read_unit(jid):
                breaches.append(
                    f"zombie timer: job '{jid}' was deregistered "
                    f"({job['tombstoned']}) but its unit is still on disk")

        # orphans: timers the ledger doesn't know
        known = set(self.state.job_ids(include_internal=True,
                                       include_tombstoned=True))
        for tid in self.systemd.list_timers():
            if tid not in known:
                breaches.append(
                    f"orphan timer: '{tid}' fires on this host but the "
                    f"ledger has never heard of it")

        # the deafness doctrine, pointed at the watcher itself
        verifies = self.state.read_verifies()
        last_verify = verifies[-1]["ts"] if verifies else None
        verifier_age_s = None
        if VERIFIER_JOB in active:
            vjob = self.state.load_job(VERIFIER_JOB)
            horizon = (vjob["cadence_seconds"]
                       * walls["verifier_horizon_factor"])
            if last_verify is None:
                if external:
                    breaches.append(
                        "deaf verifier: no reconciliation has EVER run "
                        "while jobs are registered — accounting unproven "
                        "is accounting assumed")
            else:
                verifier_age_s = int(
                    (now - _parse(last_verify)).total_seconds())
                if verifier_age_s > horizon:
                    breaches.append(
                        f"deaf verifier: last reconciliation {last_verify} "
                        f"({verifier_age_s}s ago) exceeds its own horizon "
                        f"({horizon}s = cadence x "
                        f"{walls['verifier_horizon_factor']}) — the watcher "
                        f"has no exemption from the deafness doctrine")
        elif external:
            breaches.append(
                "verifier unregistered: jobs are on the bell but the "
                "verifier is not on its own — silence about silence")

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "jobs": jobs_report,
            "job_count": {"active": len(external),
                          "max_jobs": walls["max_jobs"]},
            "witness_dark": witness_dark,
            "verifier": {"last_verify": last_verify,
                         "age_seconds": verifier_age_s},
            # tails quoted verbatim (every claim traces to a ledger)
            "firing_tail": self.state.read_firings()[-10:],
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
            "witness_api_base": config.get("witness_api_base"),
        })
        return out

    # -- admin (human-approved) --------------------------------------------
    def deregister(self, job_id: str) -> dict:
        approvals.consume(self.state, "deregister")
        job = self.state.load_job(job_id)
        if job.get("tombstoned"):
            raise ValueError(f"job '{job_id}' is already tombstoned "
                             f"({job['tombstoned']})")
        fires = [e for e in self.state.read_firings()
                 if e.get("event") == "fire" and e.get("job") == job_id]
        units_removed = bool(self.systemd.remove_units(job_id))
        witness_deleted = True
        try:
            self.witness.pause(job_id)
            self.witness.delete(job_id)
        except WitnessUnreachable:
            witness_deleted = False
        now = _iso(self._now())
        self.state.append_firing({
            "ts": now, "event": "deregister", "job": job_id,
            "units_removed": units_removed,
            "witness_deleted": witness_deleted})
        self.state.save_job(job_id, {**job, "tombstoned": now})
        # blast radius, printed AFTER the act records what it was
        return {"deregistered": job_id,
                "registered_since": job["registered"],
                "lifetime_firings": len(fires),
                "last_fired": fires[-1]["ts"] if fires else None,
                "units_removed": units_removed,
                "witness_deleted": witness_deleted}

    def configure(self, max_jobs: int, grace_divisor: int,
                  grace_cap_minutes: int, verifier_horizon_factor: int,
                  unwitnessed_streak_threshold: int,
                  witness_api_base: str, witness_ping_base: str) -> dict:
        approvals.consume(self.state, "configure")
        for name, v in (("max_jobs", max_jobs),
                        ("grace_divisor", grace_divisor),
                        ("grace_cap_minutes", grace_cap_minutes),
                        ("verifier_horizon_factor", verifier_horizon_factor),
                        ("unwitnessed_streak_threshold",
                         unwitnessed_streak_threshold)):
            if int(v) < 1:
                raise ValueError(f"{name} must be >= 1")
        for name, v in (("witness_api_base", witness_api_base),
                        ("witness_ping_base", witness_ping_base)):
            if not str(v).startswith(("http://", "https://")):
                raise ValueError(f"{name} must be http(s):// (the base "
                                 f"URLs are parameters, never constants)")
        self.state.init()
        config = {
            "max_jobs": int(max_jobs),
            "grace_divisor": int(grace_divisor),
            "grace_cap_minutes": int(grace_cap_minutes),
            "verifier_horizon_factor": int(verifier_horizon_factor),
            "unwitnessed_streak_threshold": int(unwitnessed_streak_threshold),
            "witness_api_base": witness_api_base.rstrip("/"),
            "witness_ping_base": witness_ping_base.rstrip("/"),
        }
        self.state.save_config(config)
        return {"configured": True, **config}
