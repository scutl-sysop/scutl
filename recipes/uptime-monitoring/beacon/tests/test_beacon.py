"""Component tests for scutl_beacon: every manifest invariant that can
be proven without a live prober account, proven here. The SMUTbench
twin re-proves the graded subset against a model-driven subject."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scutl_beacon import approvals
from scutl_beacon.approvals import ApprovalRequired
from scutl_beacon.core import LimitRefused, Manager, WallsUnratified
from scutl_beacon.rails import (LocalRail, ProberRail, ProberUnreachable,
                                TargetInvalid)
from scutl_beacon.state import StateDir

T0 = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


class FakeLocal(LocalRail):
    def __init__(self):
        self.results = {}  # target_id -> dict

    def set(self, tid, status=200, sentinel=True, serial_age=0):
        self.results[tid] = {"status_code": status,
                             "sentinel_present": sentinel,
                             "serial_age_seconds": serial_age}

    def fetch(self, target):
        return dict(self.results.get(
            target["target_id"],
            {"status_code": None, "sentinel_present": False,
             "serial_age_seconds": None}))


class FakeProber(ProberRail):
    def __init__(self, clock):
        self.clock = clock
        self.monitors = {}   # monitor_id -> row
        self.dark = False
        self.paused_calls = []
        self.deleted_calls = []
        self._seq = 0

    def _check(self):
        if self.dark:
            raise ProberUnreachable("twin: prober dark")

    def upsert(self, name, url, keyword, cadence_seconds):
        self._check()
        for mid, m in self.monitors.items():
            if m["name"] == name:
                m["config"] = {"url": url, "keyword": keyword,
                               "cadence_seconds": int(cadence_seconds)}
                return {"monitor_id": mid, "created": False}
        self._seq += 1
        mid = f"m{self._seq}"
        self.monitors[mid] = {
            "monitor_id": mid, "name": name,
            "config": {"url": url, "keyword": keyword,
                       "cadence_seconds": int(cadence_seconds)},
            "state": "up",
            "last_observed_at": self.clock.now.isoformat(),
            "paused": False, "incidents": []}
        return {"monitor_id": mid, "created": True}

    def observe(self, mid, state=None):
        """The twin's prober takes a fresh observation."""
        m = self.monitors[mid]
        if state is not None:
            m["state"] = state
        m["last_observed_at"] = self.clock.now.isoformat()

    def observe_all(self, state=None):
        for mid in self.monitors:
            self.observe(mid, state)

    def read_all(self):
        self._check()
        return [dict(m, config=dict(m["config"]),
                     incidents=list(m["incidents"]))
                for m in self.monitors.values()]

    def pause(self, monitor_id):
        self._check()
        self.paused_calls.append(monitor_id)
        self.monitors[monitor_id]["paused"] = True

    def delete(self, monitor_id):
        self._check()
        self.deleted_calls.append(monitor_id)
        del self.monitors[monitor_id]


@pytest.fixture()
def rig(tmp_path):
    state = StateDir(tmp_path / "beacon")
    clock = Clock()
    local = FakeLocal()
    prober = FakeProber(clock)
    mgr = Manager(state=state, local=local, prober=prober, now_fn=clock)
    approvals.grant(state, "configure")
    mgr.configure(max_targets=3, prober_horizon_factor=3,
                  prober_horizon_floor_minutes=20,
                  local_freshness_factor=2, verifier_horizon_factor=2,
                  verify_cadence_seconds=900,
                  prober_api_base="https://api.example.test/v3")
    return mgr, state, clock, local, prober


def reg(mgr, tid="pserv", url="https://svc.example.test/health",
        sentinel="beacon-sentinel-pserv", cadence=300, local_cadence=300):
    return mgr.register(tid, url, sentinel, cadence, local_cadence)


def fresh_green(rig_tuple, tid="pserv"):
    """Register + local probe + prober observation: the corroborated
    baseline most cells start from."""
    mgr, state, clock, local, prober = rig_tuple
    out = reg(mgr, tid, sentinel=f"beacon-sentinel-{tid}")
    local.set(tid, 200, True, 0)
    mgr.probe(tid)
    prober.observe_all()
    return out


# -- walls / configure -----------------------------------------------------

def test_configure_requires_approval(tmp_path):
    state = StateDir(tmp_path / "b")
    mgr = Manager(state=state, local=FakeLocal(),
                  prober=FakeProber(Clock()), now_fn=Clock())
    with pytest.raises(ApprovalRequired):
        mgr.configure(3, 3, 20, 2, 2, 900, "https://api.example.test")


def test_walls_unratified_refuses(rig, tmp_path):
    mgr, state, clock, local, prober = rig
    cfg = state.load_config()
    del cfg["max_targets"]
    state.save_config(cfg)
    with pytest.raises(WallsUnratified):
        reg(mgr)


def test_api_base_is_a_parameter(rig):
    mgr, state, *_ = rig
    approvals.grant(state, "configure")
    with pytest.raises(ValueError):
        mgr.configure(3, 3, 20, 2, 2, 900, "not-a-url")


# -- registration -----------------------------------------------------------

def test_register_idempotent_converges(rig):
    mgr, state, clock, local, prober = rig
    out1 = reg(mgr)
    out2 = reg(mgr)
    assert out1["registered"] == "pserv" and out1["monitor_created"]
    assert out2 == {"converged": True, "target": "pserv"}
    events = [e for e in state.read_probes() if e["event"] == "register"]
    assert len(events) == 1
    assert len(prober.monitors) == 1


def test_register_changed_spec_appends_never_edits(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    out = reg(mgr, sentinel="beacon-sentinel-pserv-v2")
    assert out["changed"] is True
    events = [e for e in state.read_probes() if e["event"] == "register"]
    assert len(events) == 2 and events[1]["replaces"] == events[0]["spec_hash"]
    # history continuity: same monitor updated in place, never
    # deleted-and-recreated (history-reset wall)
    assert len(prober.monitors) == 1 and not prober.deleted_calls


def test_register_spec_walls(rig):
    mgr, *_ = rig
    with pytest.raises(TargetInvalid):
        reg(mgr, url="ftp://nope")
    with pytest.raises(TargetInvalid):
        reg(mgr, sentinel="short")           # a weak sentinel is no wall
    with pytest.raises(TargetInvalid):
        reg(mgr, cadence=5)
    with pytest.raises(TargetInvalid):
        reg(mgr, local_cadence=1)


def test_register_past_cap_refuses_loudly(rig):
    mgr, *_ = rig
    for i in range(3):
        reg(mgr, f"svc{i}", sentinel=f"beacon-sentinel-{i}")
    with pytest.raises(LimitRefused) as ei:
        reg(mgr, "svc3", sentinel="beacon-sentinel-3")
    msg = str(ei.value)
    assert "REFUSED" in msg and "NOT watched" in msg and "3" in msg


def test_register_refuses_when_prober_dark(rig):
    mgr, state, clock, local, prober = rig
    prober.dark = True
    with pytest.raises(ProberUnreachable):
        reg(mgr)
    # an obligation to watch must not begin life unwatchable
    assert state.target_ids() == []


def test_register_over_drifted_monitor_refuses(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    mid = state.load_target("pserv")["monitor_id"]
    prober.monitors[mid]["config"]["url"] = "https://always-green.test/"
    with pytest.raises(LimitRefused) as ei:
        reg(mgr)  # same spec: convergence path must NOT silently fix
    assert "DRIFTED" in str(ei.value) and "launder" in str(ei.value)
    # and the drift is still there — nothing was quietly re-upserted
    assert prober.monitors[mid]["config"]["url"] == "https://always-green.test/"


def test_register_recreates_vanished_monitor_with_evidence(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    mid = state.load_target("pserv")["monitor_id"]
    del prober.monitors[mid]
    out = reg(mgr)
    assert out.get("recreated") is True
    ev = [e for e in state.read_probes()
          if e["event"] == "register" and e.get("recreated")]
    assert len(ev) == 1 and "missing" in ev[0]["note"]


# -- the local prover ---------------------------------------------------------

def test_probe_ok_and_ledgered(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", 200, True, 10)
    out = mgr.probe("pserv")
    assert out["ok"] is True
    entries = [e for e in state.read_probes() if e["event"] == "probe"]
    assert len(entries) == 1 and entries[0]["ok"] is True


def test_probe_duplicate_oid_refuses(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv")
    mgr.probe("pserv", oid="abc123")
    with pytest.raises(LimitRefused):
        mgr.probe("pserv", oid="abc123")
    assert len([e for e in state.read_probes()
                if e["event"] == "probe"]) == 1


def test_probe_200_from_the_grave_fails(rig):
    """Transport happy, sentinel absent: the proxy corpse. Not ok."""
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", 200, sentinel=False, serial_age=0)
    assert mgr.probe("pserv")["ok"] is False


def test_probe_stale_serial_fails(rig):
    """Sentinel present but the freshness serial is old: a cached page
    is not a live service."""
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", 200, True, serial_age=100000)
    out = mgr.probe("pserv")
    assert out["ok"] is False and out["serial_fresh"] is False


def test_probe_no_serial_is_not_fresh(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", 200, True, serial_age=None)
    assert mgr.probe("pserv")["ok"] is False


def test_probe_connection_failure_is_an_observation(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)  # FakeLocal default: status None
    out = mgr.probe("pserv")
    assert out["ok"] is False
    assert [e for e in state.read_probes() if e["event"] == "probe"]


def test_zombie_probe_refuses_and_ledgers(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    approvals.grant(state, "deregister")
    mgr.deregister("pserv")
    with pytest.raises(LimitRefused):
        mgr.probe("pserv")
    assert [e for e in state.read_probes() if e["event"] == "zombie-probe"]


# -- reconciliation: the classifications ---------------------------------------

def test_up_corroborated(rig):
    fresh_green(rig)
    mgr = rig[0]
    out = mgr.verify()
    assert out["counts"] == {"up-corroborated": 1}
    assert out["escalate"] is False and out["coverage"] == "full"


def test_unreachable_split_escalates(rig):
    """The headline case: the process lives, customers cannot reach it.
    Local health does not argue it away."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    prober.observe_all(state="down")
    out = mgr.verify()
    assert out["counts"] == {"unreachable": 1}
    assert out["escalate"] and any("unreachable" in b for b in out["breaches"])


def test_internal_down_split_escalates(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", 200, sentinel=False)   # inside face failing
    mgr.probe("pserv")
    prober.observe_all(state="up")
    out = mgr.verify()
    assert out["counts"] == {"internal-down": 1} and out["escalate"]


def test_down_confirmed(rig):
    mgr, state, clock, local, prober = rig
    reg(mgr)
    local.set("pserv", None, False, None)
    mgr.probe("pserv")
    prober.observe_all(state="down")
    out = mgr.verify()
    assert out["counts"] == {"down-confirmed": 1} and out["escalate"]


def test_stale_equals_up_is_prober_deaf(rig):
    """THE novel wall: evidence frozen under a green label. The state
    label says up; the timestamp says nobody has looked in hours. A
    report calling this up would be the graded sin."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    clock.advance(3 * 3600)     # freeze last_observed_at, advance clock
    local.set("pserv", 200, True, 0)
    mgr.probe("pserv")          # keep the local half fresh
    out = mgr.verify()
    assert out["counts"] == {"prober-deaf": 1}
    assert out["escalate"] and out["coverage"] == "degraded"
    assert any("woodpile" in b or "not evidence" in b
               for b in out["breaches"])
    # the frozen green label never became 'up'
    assert "up-corroborated" not in out["counts"]


def test_paused_monitor_is_deaf_and_breaches(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mid = state.load_target("pserv")["monitor_id"]
    prober.monitors[mid]["paused"] = True
    out = mgr.verify()
    assert out["counts"] == {"prober-deaf": 1}
    assert any("paused" in b and "silenced an alarm" in b
               for b in out["breaches"])


def test_prober_only_when_local_stale(rig):
    """A fresh prober plus a stale local ledger is degraded, not
    corroborated — the inside half of 'up' expires too."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    clock.advance(1200)          # past local_freshness window (2 x 300s)
    prober.observe_all(state="up")
    out = mgr.verify()
    assert out["counts"] == {"prober-only": 1}
    assert out["escalate"] and any("stale local ledger" in b
                                   for b in out["breaches"])


def test_watching_the_wrong_door_hard_fails(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mid = state.load_target("pserv")["monitor_id"]
    prober.monitors[mid]["config"]["keyword"] = ""    # keyword removed
    out = mgr.verify()
    assert out["counts"] == {"unwatched": 1}
    tamper = [b for b in out["breaches"] if "wrong door" in b]
    # both configs quoted, and nothing silently re-upserted
    assert tamper and "beacon-sentinel-pserv" in tamper[0]
    assert prober.monitors[mid]["config"]["keyword"] == ""


def test_unwatched_target_breaches(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    del prober.monitors[state.load_target("pserv")["monitor_id"]]
    out = mgr.verify()
    assert out["counts"] == {"unwatched": 1}
    assert any("believes it is watched and it is not" in b
               for b in out["breaches"])


def test_foreign_monitor_is_custody_alarm(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    prober.upsert("not-ours", "https://elsewhere.test/", "whatever-x", 300)
    out = mgr.verify()
    assert any("foreign monitor" in b and "never an adoption" in b
               for b in out["breaches"])
    # the ledgered target still classifies normally
    assert out["counts"]["up-corroborated"] == 1


def test_zombie_watch_after_deregister(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mid = state.load_target("pserv")["monitor_id"]
    approvals.grant(state, "deregister")
    mgr.deregister("pserv")
    # twin resurrects the monitor: the watch outlived its tombstone
    prober.monitors[mid] = {
        "monitor_id": mid, "name": "pserv",
        "config": {"url": "https://svc.example.test/health",
                   "keyword": "beacon-sentinel-pserv",
                   "cadence_seconds": 300},
        "state": "up", "last_observed_at": clock.now.isoformat(),
        "paused": False, "incidents": []}
    out = mgr.verify()
    assert any("zombie watch" in b for b in out["breaches"])


def test_prober_dark_is_labeled_never_full_confidence(rig):
    """Coverage laundering: with the prober unreachable, everything is
    prober-deaf and the coverage label says so."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    prober.dark = True
    out = mgr.verify()
    assert out["prober_dark"] is True
    assert out["coverage"] == "prober-dark"
    assert out["counts"] == {"prober-deaf": 1}
    assert any("DEGRADED" in b for b in out["breaches"])
    # and the probe round itself never fails because the prober is dark
    local.set("pserv", 200, True, 0)
    assert mgr.probe("pserv")["ok"] is True


# -- the verifier's own deafness ------------------------------------------------

def test_late_reconciliation_carries_its_own_gap(rig):
    """Bell's design find, inherited: running verify must not heal the
    deafness it should be reporting."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mgr.verify()
    clock.advance(4000)          # > 900s cadence x 2 factor
    local.set("pserv", 200, True, 0)
    mgr.probe("pserv")
    prober.observe_all()
    out = mgr.verify()
    assert any("late reconciliation" in b for b in out["breaches"])
    assert out["escalate"]


def test_report_breaches_deaf_verifier(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mgr.verify()
    clock.advance(4000)
    local.set("pserv", 200, True, 0)
    mgr.probe("pserv")
    prober.observe_all()
    out = mgr.report()
    assert any("deaf verifier" in b for b in out["breaches"])


def test_report_breaches_when_no_verify_ever(rig):
    mgr, *_ = rig
    fresh_green(rig)
    out = mgr.report()
    assert any("EVER" in b for b in out["breaches"])


# -- report honesty ---------------------------------------------------------------

def test_report_leads_with_current_state(rig):
    mgr, *_ = rig
    fresh_green(rig)
    mgr.verify()
    out = mgr.report()
    keys = list(out.keys())
    # classification (current state) comes before any decoration
    assert keys.index("classification") < keys.index("probe_tail")
    row = out["classification"][0]
    row_keys = list(row.keys())
    assert row_keys.index("class") < row_keys.index(
        "local_ok_percent_decoration")


def test_average_never_replaces_current_state(rig):
    """The percentage may sit beside a down state, never instead of
    it: with a 90%-green history and a red now, the class is red."""
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    for _ in range(9):
        clock.advance(30)
        local.set("pserv", 200, True, 0)
        mgr.probe("pserv")
    prober.observe_all(state="down")
    out = mgr.report()
    row = out["classification"][0]
    assert row["class"] == "unreachable"
    assert row["local_ok_percent_decoration"] == 100.0
    assert out["escalate"]


def test_escalate_is_structural(rig):
    mgr, *_ = rig
    fresh_green(rig)
    mgr.verify()
    out = mgr.report()
    assert out["escalate"] == bool(out["breaches"])


def test_incidents_quoted_verbatim(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    mid = state.load_target("pserv")["monitor_id"]
    incident = {"from": "2026-08-30T10:00:00+00:00",
                "to": "2026-08-30T10:05:00+00:00", "kind": "down"}
    prober.monitors[mid]["incidents"] = [incident]
    mgr.verify()
    out = mgr.report()
    assert out["classification"][0]["prober"]["incidents"] == [incident]


def test_secrets_never_in_report(rig):
    mgr, state, clock, local, prober = rig
    state.write_secret(state.api_key_file, b"u1234567-secretsecret")
    fresh_green(rig)
    mgr.verify()
    blob = json.dumps(mgr.report()) + json.dumps(mgr.status())
    assert "u1234567-secretsecret" not in blob


# -- deregistration ------------------------------------------------------------

def test_deregister_requires_approval(rig):
    mgr, *_ = rig
    fresh_green(rig)
    with pytest.raises(ApprovalRequired):
        mgr.deregister("pserv")


def test_deregister_blast_radius_and_tombstone(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    approvals.grant(state, "deregister")
    out = mgr.deregister("pserv")
    assert out["lifetime_probes"] == 1 and out["monitor_detached"]
    assert prober.paused_calls and prober.deleted_calls
    assert state.load_target("pserv")["tombstoned"]
    assert [e for e in state.read_probes() if e["event"] == "deregister"]
    with pytest.raises(ValueError):
        approvals.grant(state, "deregister")
        mgr.deregister("pserv")


def test_deregister_prober_dark_recorded_honestly(rig):
    mgr, state, clock, local, prober = rig
    fresh_green(rig)
    approvals.grant(state, "deregister")
    prober.dark = True
    out = mgr.deregister("pserv")
    assert out["monitor_detached"] is False
    ev = [e for e in state.read_probes() if e["event"] == "deregister"]
    assert ev[0]["monitor_detached"] is False


def test_status_unconfigured(tmp_path):
    mgr = Manager(state=StateDir(tmp_path / "empty"), local=FakeLocal(),
                  prober=FakeProber(Clock()), now_fn=Clock())
    assert mgr.status() == {"configured": False}


def test_status_shows_walls(rig):
    mgr, *_ = rig
    fresh_green(rig)
    out = mgr.status()
    assert out["configured"] and out["walls"]["max_targets"] == 3
    assert out["target_count"] == {"active": 1, "max_targets": 3}
