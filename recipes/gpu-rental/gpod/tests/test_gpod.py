"""gpod component tests against a twin provider.

The twin owns the pod table and can lie: delete-accepted-pod-persists
(the undead), foreign pods appearing, prices differing from catalog —
the manifest's contracts.failure_modes each get a test.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from scutl_gpod import approvals
from scutl_gpod.approvals import ApprovalRequired
from scutl_gpod.core import LimitRefused, Manager, Undead
from scutl_gpod.network import Absent
from scutl_gpod.state import Decommissioned, StateDir


class TwinPods:
    def __init__(self, undead: bool = False):
        self.pods: dict[str, dict] = {}
        self.undead = undead
        self._n = 0
        self.base = "https://twin.example.invalid/v1"

    def seed_foreign(self, pod_id: str = "foreign-1"):
        self.pods[pod_id] = {"id": pod_id, "name": "console-made",
                             "desiredStatus": "RUNNING"}

    def create_pod(self, spec: dict) -> dict:
        self._n += 1
        pod = {"id": f"pod-{self._n}", "name": spec["name"],
               "desiredStatus": "RUNNING",
               "costPerHr": 0.74, "imageName": spec["imageName"],
               "spec": spec}
        self.pods[pod["id"]] = pod
        return pod

    def get_pod(self, pod_id: str) -> dict:
        if pod_id not in self.pods:
            raise Absent()
        return self.pods[pod_id]

    def delete_pod(self, pod_id: str) -> None:
        if not self.undead:
            self.pods.pop(pod_id, None)

    def list_pods(self) -> list[dict]:
        return list(self.pods.values())


class TwinStock:
    def __init__(self, prices: dict | None = None):
        self.prices = prices if prices is not None else {
            "NVIDIA GeForce RTX 4090": Decimal("0.74")}

    def catalog_price(self, gpu_type: str):
        return self.prices.get(gpu_type)

    def availability(self, gpu_type: str, region=None):
        return {"gpu_type": gpu_type, "region": region,
                "stock": {"EU-RO-1": "High"}, "available": True}


def rig(tmp_path, undead=False, prices=None):
    state = StateDir(tmp_path / "state")
    state.init()
    state.write_secret(state.api_key_file, b"TWIN_API_KEY_SHH")
    pods = TwinPods(undead=undead)
    mgr = Manager(state=state, pods=pods, stock=TwinStock(prices),
                  sleep_fn=lambda s: None)
    approvals.grant(state, "configure")
    mgr.configure(["NVIDIA GeForce RTX 4090"], Decimal("0.80"), 1,
                  "EU-RO-1")
    return state, mgr, pods


# -- walls: everything refuses BEFORE the API call ---------------------

def test_create_refuses_unlisted_gpu_type(tmp_path):
    state, mgr, pods = rig(tmp_path)
    with pytest.raises(LimitRefused):
        mgr.create("NVIDIA H100 80GB HBM3", "x")
    assert pods.pods == {}


def test_create_refuses_over_ceiling(tmp_path):
    state, mgr, pods = rig(
        tmp_path, prices={"NVIDIA GeForce RTX 4090": Decimal("0.99")})
    with pytest.raises(LimitRefused):
        mgr.create("NVIDIA GeForce RTX 4090", "x")
    assert pods.pods == {}


def test_create_refuses_unpriceable_type(tmp_path):
    # price-differs-from-catalog's hard edge: no catalog price at all
    state, mgr, pods = rig(tmp_path, prices={})
    with pytest.raises(LimitRefused):
        mgr.create("NVIDIA GeForce RTX 4090", "x")
    assert pods.pods == {}


def test_create_refuses_at_max_pods(tmp_path):
    state, mgr, pods = rig(tmp_path)
    mgr.create("NVIDIA GeForce RTX 4090", "one")
    with pytest.raises(LimitRefused):
        mgr.create("NVIDIA GeForce RTX 4090", "two")
    assert len(pods.pods) == 1


def test_create_refuses_non_devel_image(tmp_path):
    state, mgr, pods = rig(tmp_path)
    with pytest.raises(LimitRefused):
        mgr.create("NVIDIA GeForce RTX 4090", "x",
                   image="nvidia/cuda:12.4.1-runtime-ubuntu22.04")
    assert pods.pods == {}


def test_create_spec_checklist_and_log_before_return(tmp_path):
    state, mgr, pods = rig(tmp_path)
    out = mgr.create("NVIDIA GeForce RTX 4090", "grade-1")
    spec = pods.pods[out["id"]]["spec"]
    assert "22/tcp" in spec["ports"]
    assert "dockerStartCmd" not in spec and "dockerEntrypoint" not in spec
    assert "-devel" in spec["imageName"]
    events = state.read_rental_events()
    assert events[-1]["event"] == "created" and events[-1]["id"] == out["id"]
    assert events[-1]["hourly_usd"] == "0.74"


# -- foreign pods: reported, never touched -----------------------------

def test_foreign_pod_reported_never_destroyed(tmp_path):
    state, mgr, pods = rig(tmp_path)
    pods.seed_foreign()
    st = mgr.status()
    assert st["foreign_pods"] == ["foreign-1"]
    with pytest.raises(LimitRefused):
        mgr.destroy("foreign-1")
    out = mgr.destroy_all()
    assert out["count"] == 0
    assert "foreign-1" in pods.pods


# -- destroy: verified or screaming ------------------------------------

def test_destroy_verifies_gone_and_closes_rental(tmp_path):
    state, mgr, pods = rig(tmp_path)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    out = mgr.destroy(pod["id"])
    assert out["verified_gone"] is True
    events = [e["event"] for e in state.read_rental_events()]
    assert events == ["created", "destroy-requested", "destroy-verified"]
    assert state.open_rentals() == {}


def test_undead_destroy_screams_and_leaves_rental_open(tmp_path):
    state, mgr, pods = rig(tmp_path, undead=True)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    with pytest.raises(Undead) as e:
        mgr.destroy(pod["id"])
    assert "billing may still be accruing" in str(e.value)
    assert pod["id"] in state.open_rentals()
    # human kills it in the console, then re-runs destroy: idempotent
    # over absence, records verified-gone
    pods.undead = False
    pods.pods.pop(pod["id"])
    out = mgr.destroy(pod["id"])
    assert out["verified_gone"] is True
    assert state.open_rentals() == {}


def test_destroy_all_covers_every_open_rental(tmp_path):
    state, mgr, pods = rig(tmp_path)
    a = mgr.create("NVIDIA GeForce RTX 4090", "a")
    mgr.destroy(a["id"])
    b = mgr.create("NVIDIA GeForce RTX 4090", "b")
    out = mgr.destroy_all()
    assert out["destroyed"] == [b["id"]]
    assert state.open_rentals() == {}


# -- volume: attach-only, cost visible ---------------------------------

def test_volume_attached_from_config_and_costed_in_status(tmp_path):
    state, mgr, pods = rig(tmp_path)
    config = state.load_config()
    config["volume"] = {"id": "vol-1", "name": "models", "size_gb": 45,
                        "data_center": "EU-RO-1"}
    state.save_config(config)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    assert pods.pods[pod["id"]]["spec"]["networkVolumeId"] == "vol-1"
    vol = mgr.status()["volume"]
    assert vol["monthly_usd_estimate"] == "3.15"
    assert "standing spend" in vol["note"]


# -- restart: everything derives from the log --------------------------

def test_counters_survive_restart(tmp_path):
    state, mgr, pods = rig(tmp_path)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    mgr2 = Manager(state=StateDir(state.root), pods=pods,
                   stock=TwinStock(), sleep_fn=lambda s: None)
    assert mgr2.status()["live_pods"] == 1
    with pytest.raises(LimitRefused):
        mgr2.create("NVIDIA GeForce RTX 4090", "y")
    mgr2.destroy(pod["id"])
    assert mgr2.status()["live_pods"] == 0


# -- admin gates + decommission ----------------------------------------

def test_configure_requires_token(tmp_path):
    state = StateDir(tmp_path / "s2")
    state.init()
    mgr = Manager(state=state, pods=TwinPods(), stock=TwinStock())
    with pytest.raises(ApprovalRequired):
        mgr.configure(["g"], Decimal("1"), 1, "EU-RO-1")


def test_decommission_refuses_open_rentals_then_gates_create_not_destroy(
        tmp_path):
    state, mgr, pods = rig(tmp_path)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    approvals.grant(state, "decommission")
    with pytest.raises(LimitRefused):
        mgr.decommission()
    mgr.destroy(pod["id"])
    mgr.decommission()
    with pytest.raises(Decommissioned):
        mgr.create("NVIDIA GeForce RTX 4090", "y")
    # destroy stays available: an open rental would still be killable.
    # (none open here; the invariant is that destroy() never calls
    # check_not_decommissioned — asserted structurally:)
    import inspect
    assert "check_not_decommissioned" not in inspect.getsource(
        Manager.destroy)


# -- secrets ------------------------------------------------------------

def test_key_never_in_outputs_or_log(tmp_path):
    state, mgr, pods = rig(tmp_path)
    pod = mgr.create("NVIDIA GeForce RTX 4090", "x")
    blob = json.dumps([mgr.status(), pod, state.read_rental_events()])
    assert "TWIN_API_KEY_SHH" not in blob
