"""Acceptance tests for the bell component (recipe #11 rev 1).

Each block maps to a manifest verify item (recipes/scheduled-jobs/
recipe.yaml). The fakes implement contracts.wire: FakeSystemd owns the
calendar, the units, and the clock's slots; FakeWitness owns the checks
and the ping log, and can go dark, drop pings, or grow foreign rids —
exactly the surface the mocked-twin bench will drive.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scutl_bell import approvals
from scutl_bell.approvals import ApprovalRequired
from scutl_bell.core import VERIFIER_JOB, LimitRefused, Manager, WallsUnratified
from scutl_bell.rails import (InvalidSchedule, SystemdRail, WitnessRail,
                              WitnessUnreachable)
from scutl_bell.state import NotConfigured, StateDir, UnknownJob

T0 = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)  # 600s-aligned
PING_KEY = "pk-vErYsEcReT-000"


class FakeSystemd(SystemdRail):
    """Fake calendar grammar: 'every:<seconds>' (+ ' tz:<zone>' to carry
    a timezone); slots are epoch-aligned multiples of the cadence."""

    def __init__(self):
        self.units: dict[str, str] = {}      # job_id -> spec_hash on disk
        self.orphans: list[str] = []
        self.stubborn = False                # remove_units leaves zombies
        self.exit_status = 0

    def calendar_parse(self, expr):
        parts = expr.split()
        if not parts or not parts[0].startswith("every:"):
            raise InvalidSchedule(f"unparseable '{expr}'")
        tz = None
        for p in parts[1:]:
            if p.startswith("tz:"):
                tz = p[3:]
        return {"normalized": parts[0], "tz": tz, "next_elapse_utc": None}

    def cadence_seconds(self, normalized):
        return int(normalized.split(":", 1)[1])

    def render_units(self, job):
        self.units[job["job_id"]] = job["spec_hash"]

    def read_unit(self, job_id):
        if job_id in self.units:
            return {"spec_hash": self.units[job_id]}
        return None

    def remove_units(self, job_id):
        if self.stubborn:
            return False
        self.units.pop(job_id, None)
        return True

    def list_timers(self):
        return sorted(list(self.units) + self.orphans)

    def slot_for(self, normalized, now):
        c = self.cadence_seconds(normalized)
        slot = int(now.timestamp()) // c * c
        st = datetime.fromtimestamp(slot, tz=timezone.utc)
        return st.isoformat(), int(now.timestamp() - slot)

    def slots_between(self, normalized, t0, t1):
        c = self.cadence_seconds(normalized)
        first = (int(t0.timestamp()) // c + 1) * c
        out = []
        while first <= int(t1.timestamp()):
            out.append(datetime.fromtimestamp(
                first, tz=timezone.utc).isoformat())
            first += c
        return out

    def run_argv(self, job, rid):
        return {"exit_status": self.exit_status, "duration_ms": 5}


class FakeWitness(WitnessRail):
    def __init__(self):
        self.checks: dict[str, dict] = {}
        self.dark = False

    def _guard(self):
        if self.dark:
            raise WitnessUnreachable("witness dark (fake)")

    def upsert(self, slug, schedule, grace_seconds):
        self._guard()
        created = slug not in self.checks
        self.checks.setdefault(slug, {
            "uuid": f"uuid-{slug}", "pings": [], "status": "up",
            "paused": False})
        self.checks[slug].update(schedule=schedule, grace=grace_seconds)
        return {"uuid": self.checks[slug]["uuid"], "created": created}

    def ping(self, slug, kind, rid):
        self._guard()
        self.checks.setdefault(slug, {"uuid": f"uuid-{slug}", "pings": [],
                                      "status": "up", "paused": False})
        self.checks[slug]["pings"].append({"rid": rid, "kind": kind,
                                           "at": "fake"})
        return True

    def read(self, slug):
        self._guard()
        if slug not in self.checks or self.checks[slug].get("deleted"):
            return {"status": "absent", "last_ping": None, "pings": []}
        c = self.checks[slug]
        return {"status": c["status"], "last_ping": None,
                "pings": list(c["pings"])}

    def pause(self, slug):
        self._guard()
        if slug in self.checks:
            self.checks[slug]["paused"] = True

    def delete(self, slug):
        self._guard()
        if slug in self.checks:
            self.checks[slug]["deleted"] = True


@pytest.fixture()
def rig(tmp_path):
    state = StateDir(tmp_path / "state")
    clock = {"now": T0}
    sysd, wit = FakeSystemd(), FakeWitness()
    mgr = Manager(state=state, systemd=sysd, witness=wit,
                  now_fn=lambda: clock["now"])
    approvals.grant(state, "configure")
    mgr.configure(3, 4, 60, 2, 3, "http://api.test", "http://ping.test")
    state.write_secret(state.ping_key_file, PING_KEY.encode())
    return state, mgr, clock, sysd, wit


def tick(clock, seconds):
    clock["now"] = clock["now"] + timedelta(seconds=seconds)


def register(mgr, jid="pulse", schedule="every:600", argv=None):
    return mgr.register(jid, argv or ["true"], schedule)


# -- idempotent register (verify: idempotent register) -------------------

def test_register_twice_converges(rig):
    state, mgr, clock, sysd, wit = rig
    r1 = register(mgr)
    assert r1["registered"] == "pulse" and r1["witness_created"]
    assert r1["grace_seconds"] == 150   # 600/4, floor 60, cap 3600
    r2 = register(mgr)
    assert r2 == {"converged": True, "job": "pulse",
                  "witness_created": False}
    lines = [e for e in state.read_firings() if e["event"] == "register"]
    assert len(lines) == 1              # convergence appends nothing
    assert len(wit.checks) == 1


def test_changed_spec_is_a_new_registration_line(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    r = register(mgr, argv=["true", "-v"])
    assert r["changed"] is True
    lines = [e for e in state.read_firings() if e["event"] == "register"]
    assert len(lines) == 2 and lines[1]["replaces"] == lines[0]["spec_hash"]


# -- the parse wall (verify: parse wall) ---------------------------------

def test_unparseable_schedule_refuses_before_any_side_effect(rig):
    state, mgr, clock, sysd, wit = rig
    with pytest.raises(InvalidSchedule):
        register(mgr, schedule="garbage")
    assert not sysd.units and not wit.checks
    assert not state.job_ids()


def test_timezone_schedule_refused_utc_only(rig):
    state, mgr, clock, sysd, wit = rig
    with pytest.raises(InvalidSchedule, match="UTC only"):
        register(mgr, schedule="every:600 tz:Asia/Tokyo")
    assert not sysd.units and not wit.checks


def test_register_refuses_when_witness_dark(rig):
    state, mgr, clock, sysd, wit = rig
    wit.dark = True
    with pytest.raises(WitnessUnreachable):
        register(mgr)
    assert not state.job_ids()   # an obligation never begins life
    assert not sysd.units        # uncorroboratable


# -- the cap, loud (verify: register past cap) ---------------------------

def test_register_past_cap_refuses_loudly(rig):
    state, mgr, clock, sysd, wit = rig
    for jid in ("a", "b", "c"):
        register(mgr, jid=jid)
    with pytest.raises(LimitRefused) as ei:
        register(mgr, jid="d")
    msg = str(ei.value)
    assert "3 job(s)" in msg and "REFUSED" in msg and "'d' is NOT" in msg
    assert "d" not in state.job_ids()
    # internal jobs never count against the wall
    mgr.register_verifier("every:600")
    assert VERIFIER_JOB in state.job_ids(include_internal=True)


# -- clean fire (verify: clean fire) -------------------------------------

def test_clean_fire_ledger_then_witness_rid_joined(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    out = mgr.fire("pulse")
    assert out["kind"] == "on-time" and out["witnessed"] is True
    fires = [e for e in state.read_firings() if e["event"] == "fire"]
    assert len(fires) == 1 and fires[0]["exit_status"] == 0
    kinds = [p["kind"] for p in wit.checks["pulse"]["pings"]]
    assert kinds == ["start", "success"]
    assert all(p["rid"] == out["rid"] for p in wit.checks["pulse"]["pings"])
    v = mgr.verify()
    assert v["counts"]["fired-and-witnessed"] == 1
    assert v["escalate"] is False and v["breaches"] == []


def test_nonzero_exit_pings_the_status_and_records_it(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    sysd.exit_status = 3
    tick(clock, 600)
    out = mgr.fire("pulse")
    assert out["exit_status"] == 3 and out["witnessed"] is True
    assert wit.checks["pulse"]["pings"][-1]["kind"] == "3"


# -- exactly-once (verify: duplicate firing) -----------------------------

def test_duplicate_rid_refuses_slot_counts_once(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    mgr.fire("pulse", rid="r-1")
    with pytest.raises(LimitRefused, match="exactly-once"):
        mgr.fire("pulse", rid="r-1")
    assert len([e for e in state.read_firings()
                if e["event"] == "fire"]) == 1


# -- missed slot (verify: missed slot) -----------------------------------

def test_missed_slot_breaches_with_slot_named(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600 + 151)      # one slot due, grace (150s) expired
    v = mgr.verify()
    assert v["counts"]["missed"] == 1 and v["escalate"] is True
    assert any("missed slot" in b and "pulse" in b for b in v["breaches"])
    slot_iso = (T0 + timedelta(seconds=600)).isoformat()
    assert any(slot_iso in b for b in v["breaches"])   # the slot, NAMED


def test_slot_inside_grace_is_pending_not_missed(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600 + 10)       # due 10s ago, grace is 150s
    v = mgr.verify()
    assert v["counts"]["pending"] == 1 and v["counts"]["missed"] == 0
    assert v["escalate"] is False


# -- catch-up labeled (verify: catch-up labeled) -------------------------

def test_catchup_repairs_the_slot_but_never_impersonates_it(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600 + 200)      # 200s late > 150s grace: a catch-up
    out = mgr.fire("pulse")
    assert out["kind"] == "catchup"
    fires = [e for e in state.read_firings() if e["event"] == "fire"]
    assert fires[0]["kind"] == "catchup" and fires[0]["late_seconds"] == 200
    v = mgr.verify()
    assert v["counts"]["catchup"] == 1 and v["counts"]["missed"] == 0
    assert v["escalate"] is False   # repaired — but never laundered


# -- witness dark at fire time (verify: witness dark) --------------------

def test_witness_dark_never_fails_the_job_and_streak_escalates(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    wit.dark = True
    for _ in range(3):
        tick(clock, 600)
        out = mgr.fire("pulse")     # no exception: the job still runs
        assert out["witnessed"] is False
    fires = [e for e in state.read_firings() if e["event"] == "fire"]
    assert all(f["witnessed_start"] is False for f in fires)
    v = mgr.verify()                # witness.read is dark too: honest
    assert v["witness_dark"] is True
    assert v["counts"]["fired-unwitnessed"] == 3
    assert any("unwitnessed streak" in b and "3" in b
               for b in v["breaches"])
    assert v["escalate"] is True


def test_single_unwitnessed_run_is_degraded_not_escalated(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    wit.dark = True
    tick(clock, 600)
    mgr.fire("pulse")
    wit.dark = False
    v = mgr.verify()
    assert v["counts"]["fired-unwitnessed"] == 1
    assert not any("unwitnessed streak" in b for b in v["breaches"])


# -- the deaf verifier (verify: deaf verifier) ---------------------------

def test_verifier_never_run_while_jobs_registered_breaches(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    mgr.register_verifier("every:600")
    rep = mgr.report()
    assert any("EVER" in b for b in rep["breaches"])
    assert rep["escalate"] is True


def test_verifier_past_its_own_horizon_breaches(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    mgr.register_verifier("every:600")
    tick(clock, 600)
    mgr.fire("pulse")
    mgr.verify()
    rep = mgr.report()
    assert not any("deaf verifier" in b for b in rep["breaches"])
    tick(clock, 1201)           # horizon = 600 x 2
    rep = mgr.report()
    assert any("deaf verifier" in b and "exemption" in b
               for b in rep["breaches"])


def test_unregistered_verifier_with_jobs_is_itself_a_breach(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    rep = mgr.report()
    assert any("verifier unregistered" in b for b in rep["breaches"])


# -- tamper and the three-way diff (verify: schedule tamper) -------------

def test_unit_drift_is_integrity_breach_never_rerendered(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    sysd.units["pulse"] = "deadbeef" * 8
    rep = mgr.report()
    assert any("schedule tamper" in b for b in rep["breaches"])
    assert sysd.units["pulse"] == "deadbeef" * 8   # no silent re-render


def test_missing_unit_breaches(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    del sysd.units["pulse"]
    rep = mgr.report()
    assert any("unit missing" in b for b in rep["breaches"])


def test_absent_witness_check_breaches(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    wit.checks["pulse"]["deleted"] = True
    rep = mgr.report()
    assert any("witness check absent" in b for b in rep["breaches"])


# -- foreign ping (verify: foreign ping) ---------------------------------

def test_foreign_rid_is_custody_alarm_not_success(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    mgr.fire("pulse")
    wit.checks["pulse"]["pings"].append(
        {"rid": "evil-rid", "kind": "success", "at": "fake"})
    v = mgr.verify()
    assert any("foreign ping" in b and "evil-rid" in b
               for b in v["breaches"])
    assert v["escalate"] is True


# -- zombie and orphan (verify: zombie job / orphan surfaces) ------------

def test_zombie_fire_refuses_and_leaves_evidence(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    approvals.grant(state, "deregister")
    mgr.deregister("pulse")
    tick(clock, 600)            # the zombie rings after the funeral
    with pytest.raises(LimitRefused, match="zombie"):
        mgr.fire("pulse")
    assert any(e["event"] == "zombie-fire"
               for e in state.read_firings())
    v = mgr.verify()
    assert any("zombie job" in b for b in v["breaches"])


def test_stubborn_units_after_deregister_breach_as_zombie_timer(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    sysd.stubborn = True
    approvals.grant(state, "deregister")
    out = mgr.deregister("pulse")
    assert out["units_removed"] is False
    rep = mgr.report()
    assert any("zombie timer" in b for b in rep["breaches"])


def test_orphan_timer_breaches_on_sight(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    sysd.orphans.append("mystery")
    rep = mgr.report()
    assert any("orphan timer" in b and "mystery" in b
               for b in rep["breaches"])


# -- deregistration is consented (verify: alarm-silencing) ---------------

def test_deregister_without_approval_refuses(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    with pytest.raises(ApprovalRequired):
        mgr.deregister("pulse")
    assert not state.load_job("pulse").get("tombstoned")
    assert not wit.checks["pulse"].get("deleted")


def test_deregister_with_approval_tombstones_and_reports_blast(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    mgr.fire("pulse")
    approvals.grant(state, "deregister")
    out = mgr.deregister("pulse")
    assert out["lifetime_firings"] == 1 and out["last_fired"]
    assert out["units_removed"] and out["witness_deleted"]
    assert state.load_job("pulse")["tombstoned"]
    assert "pulse" not in state.job_ids()
    v = mgr.verify()      # a deregistered job's silence is rest
    assert v["counts"]["missed"] == 0


def test_revival_after_tombstone_is_a_new_registration(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    approvals.grant(state, "deregister")
    mgr.deregister("pulse")
    r = register(mgr)
    assert r.get("registered") == "pulse"
    assert not state.load_job("pulse").get("tombstoned")


# -- honesty of the report (verify: disclosure-is-not-alarm) -------------

def test_escalate_derives_from_breaches_in_code(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    mgr.register_verifier("every:600")
    mgr.verify()
    rep = mgr.report()
    assert rep["breaches"] == [] and rep["escalate"] is False
    del sysd.units["pulse"]
    rep = mgr.report()
    assert rep["breaches"] and rep["escalate"] is True


def test_report_quotes_ledger_tails_verbatim(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    mgr.fire("pulse")
    mgr.verify()
    rep = mgr.report()
    assert rep["firing_tail"][-1] == state.read_firings()[-1]
    assert rep["verify_tail"][-1] == state.read_verifies()[-1]


# -- secrets (verify: secrets) -------------------------------------------

def test_no_secret_in_any_output(rig):
    state, mgr, clock, sysd, wit = rig
    register(mgr)
    tick(clock, 600)
    outputs = [mgr.fire("pulse"), mgr.verify(), mgr.report(),
               mgr.status()]
    approvals.grant(state, "deregister")
    outputs.append(mgr.deregister("pulse"))
    blob = json.dumps(outputs)
    assert PING_KEY not in blob


# -- walls (setup: policy-ratified) --------------------------------------

def test_unconfigured_and_unratified_refuse(tmp_path):
    state = StateDir(tmp_path / "state")
    mgr = Manager(state=state, systemd=FakeSystemd(),
                  witness=FakeWitness(), now_fn=lambda: T0)
    with pytest.raises(NotConfigured):
        mgr.register("x", ["true"], "every:600")
    state.save_config({"max_jobs": 3})   # four walls missing
    with pytest.raises(WallsUnratified, match="unratified"):
        mgr.register("x", ["true"], "every:600")


def test_unknown_job_and_bad_ids(rig):
    state, mgr, clock, sysd, wit = rig
    with pytest.raises(UnknownJob):
        mgr.fire("ghost")
    with pytest.raises(ValueError):
        register(mgr, jid="_sneaky")     # internal prefix is internal
    with pytest.raises(ValueError):
        mgr.register("ok", [], "every:600")
