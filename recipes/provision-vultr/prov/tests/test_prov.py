"""Provision acceptance tests against a mocked provider.

These map to the manifest's verify section (limit/ceiling/destroy-ungated/
subzone/key-hygiene/reconcile/decommission-order probes); the live
acceptance — real create, merchant bootstrap, sale, destroy — runs once
per rev on run day, not here. The mock honors the contracts block in
recipe.yaml; every refusal probe also asserts NO provider call was made,
because "refused in code, before the API call" is the invariant.
"""

import json
from decimal import Decimal

import pytest

from scutl_prov import approvals
from scutl_prov.approvals import ApprovalRequired
from scutl_prov.core import LimitRefused, Manager
from scutl_prov.state import StateDir


class MockVultr:
    """Implements the contracts block; records every mutating call."""

    PLANS = [
        {"id": "vc2-1c-1gb", "hourly_cost": Decimal("0.0069")},
        {"id": "vc2-2c-4gb", "hourly_cost": Decimal("0.0247")},
        {"id": "vc2-6c-16gb", "hourly_cost": Decimal("0.1096")},
    ]

    def __init__(self):
        self.instances = {}
        self.records = []
        self.calls = []
        self._n = 0

    def plans(self):
        return [dict(p) for p in self.PLANS]

    def create_instance(self, plan, region, label, os_id=2136):
        self.calls.append(("create", plan, region, label))
        self._n += 1
        inst = {"id": f"vm-{self._n}", "plan": plan, "region": region,
                "label": label, "status": "pending", "power_status": "running",
                "main_ip": f"203.0.113.{self._n}", "date_created": "2026-08-15"}
        self.instances[inst["id"]] = inst
        return dict(inst)

    def list_instances(self):
        return [dict(i) for i in self.instances.values()]

    def destroy_instance(self, instance_id):
        self.calls.append(("destroy", instance_id))
        self.instances.pop(instance_id, None)

    def list_records(self, domain):
        return [dict(r) for r in self.records if r["domain"] == domain]

    def create_record(self, domain, name, rtype, value, ttl=300):
        self.calls.append(("dns-set", domain, name, rtype, value))
        rec = {"id": f"rec-{len(self.records)+1}", "domain": domain,
               "name": name, "type": rtype, "data": value}
        self.records.append(rec)
        return dict(rec)

    def delete_record(self, domain, record_id):
        self.calls.append(("dns-delete", domain, record_id))
        self.records = [r for r in self.records if r["id"] != record_id]


SECRET = "vultr-key-hunter2-do-not-print"


@pytest.fixture
def mock():
    return MockVultr()


@pytest.fixture
def manager(tmp_path, mock):
    state = StateDir(tmp_path / "provision")
    m = Manager(state=state, client=mock)
    approvals.grant(state, "configure")
    m.configure(["vc2-1c-1gb", "vc2-6c-16gb"], ["ewr", "fra"],
                max_instances=2, max_hourly_usd=Decimal("0.018"),
                dns_subzone="lab.scutl.example")
    keyfile = tmp_path / "vultr.key"
    keyfile.write_text(SECRET + "\n")
    approvals.grant(state, "set-key")
    m.set_key(str(keyfile))
    return m


def test_admin_ops_require_approval(tmp_path, mock):
    m = Manager(StateDir(tmp_path / "p"), mock)
    with pytest.raises(ApprovalRequired):
        m.configure(["vc2-1c-1gb"], ["ewr"], 1, Decimal("0.01"))


def test_create_inside_limits_succeeds_and_logs(manager, mock):
    out = manager.create("vc2-1c-1gb", "ewr", "merchant-1")
    assert out["id"] == "vm-1" and out["main_ip"]
    events = manager.state.read_instance_events()
    assert [e["event"] for e in events] == ["created"]
    assert events[0]["hourly_usd"] == "0.0069"


def test_limits_probe_refused_in_code_no_api_call(manager, mock):
    # non-allowlisted plan, non-allowlisted region, over-ceiling plan —
    # all refused before any provider call (verify: limits probe).
    for plan, region in (("vc2-2c-4gb", "ewr"),      # plan not allowlisted
                         ("vc2-1c-1gb", "syd"),      # region not allowlisted
                         ("vc2-6c-16gb", "ewr")):    # allowlisted but 0.1096 > 0.018
        with pytest.raises(LimitRefused):
            manager.create(plan, region, "x")
    assert mock.calls == []  # nothing left the box


def test_ceiling_probe_instance_count(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "a")
    manager.create("vc2-1c-1gb", "ewr", "b")
    with pytest.raises(LimitRefused, match="max_instances"):
        manager.create("vc2-1c-1gb", "ewr", "c")
    manager.destroy("vm-1")
    assert manager.create("vc2-1c-1gb", "ewr", "c")["id"] == "vm-3"


def test_destroy_is_never_gated(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "a")
    # no approval token exists; destroy works
    assert manager.destroy("vm-1") == {"destroyed": "vm-1"}
    # ...and still works after decommission
    approvals.grant(manager.state, "decommission")
    manager.decommission()
    manager.state.decommission_marker.exists()
    inst = mock.create_instance("vc2-1c-1gb", "ewr", "stray")
    manager.state.append_instance_event(
        {"ts": "t", "event": "created", "id": inst["id"]})
    assert manager.destroy(inst["id"]) == {"destroyed": inst["id"]}
    # while create refuses
    from scutl_prov.state import Decommissioned
    with pytest.raises(Decommissioned):
        manager.create("vc2-1c-1gb", "ewr", "nope")


def test_subzone_probe(manager, mock):
    with pytest.raises(LimitRefused, match="outside the delegated subzone"):
        manager.dns_set("evil.example.com", "A", "203.0.113.9")
    with pytest.raises(LimitRefused):
        manager.dns_set("notlab.scutl.example.attacker.net", "A", "203.0.113.9")
    assert mock.calls == []
    out = manager.dns_set("pay.lab.scutl.example", "A", "203.0.113.9")
    assert out["set"]["record_id"] == "rec-1"
    assert mock.records[0]["name"] == "pay"  # relative to the subzone
    deleted = manager.dns_delete("pay.lab.scutl.example", "a")
    assert deleted["deleted"] == ["rec-1"]


def test_dns_disabled_without_subzone(tmp_path, mock):
    state = StateDir(tmp_path / "p2")
    m = Manager(state, mock)
    approvals.grant(state, "configure")
    m.configure(["vc2-1c-1gb"], ["ewr"], 1, Decimal("0.018"))  # no subzone
    with pytest.raises(LimitRefused, match="DNS writes are disabled"):
        m.dns_set("pay.lab.scutl.example", "A", "203.0.113.9")


def test_key_hygiene_probe(manager, mock, tmp_path, capsys):
    # every op's output, and the state's logs, contain zero key bytes
    manager.create("vc2-1c-1gb", "ewr", "a")
    outputs = [manager.status(), manager.list(),
               manager.dns_list(), manager.destroy_all()]
    blob = json.dumps(outputs)
    assert SECRET not in blob
    assert SECRET not in manager.state.instances_log.read_text()
    assert oct(manager.state.api_key_file.stat().st_mode & 0o777) == "0o600"
    assert not (tmp_path / "vultr.key").exists()  # set-key consumed the source


def test_reconcile_probe_foreign_never_touched(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "mine")
    foreign = mock.create_instance("vc2-6c-16gb", "ewr", "not-mine")
    status = manager.status()
    assert status["live_instances"] == 1
    assert status["foreign_instances"] == [foreign["id"]]
    manager.destroy_all()
    assert foreign["id"] in mock.instances          # untouched
    with pytest.raises(LimitRefused, match="never touched"):
        manager.destroy(foreign["id"])


def test_lost_at_provider_is_reported(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "a")
    del mock.instances["vm-1"]  # provider-side disappearance
    status = manager.status()
    assert status["lost_at_provider"] == ["vm-1"]
    assert status["live_instances"] == 0


def test_decommission_order_probe(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "a")
    approvals.grant(manager.state, "decommission")
    with pytest.raises(LimitRefused, match="destroy-all first"):
        manager.decommission()
    manager.destroy_all()
    approvals.grant(manager.state, "decommission")
    out = manager.decommission()
    assert "not revocation" in out["note"]


def test_restart_recovers_live_set_from_log(manager, mock):
    manager.create("vc2-1c-1gb", "ewr", "a")
    reborn = Manager(state=StateDir(manager.state.root), client=mock)
    assert reborn.state.log_live_ids() == {"vm-1"}
    assert reborn.status()["live_instances"] == 1
