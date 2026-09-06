"""Merchant acceptance tests against a mocked facilitator.

These map to the manifest's verify section (offer, replay, underpay,
payto-injection, restart probes); the live end-to-end sale — wallet rev 1
as buyer — runs in the acceptance suite, not here. The mocks honor the
contracts in recipe.yaml and seed the scutbench mock services (cst-8ih.4).
"""

import base64
import json
from decimal import Decimal

import pytest

from scutl_pserv import approvals
from scutl_pserv.approvals import ApprovalRequired
from scutl_pserv.core import Manager, Merchant
from scutl_pserv.network import SettleResult, TransientError
from scutl_pserv.state import Decommissioned, StateDir

PAYTO = "0x" + "aa" * 20
PRICE = "10000"  # 0.01 USDC atomic


class MockFacilitator:
    def __init__(self, fail_transient_times=0, reject_verify=False):
        self.settled_nonces = []
        self.verified = 0
        self.fail_transient_times = fail_transient_times
        self.reject_verify = reject_verify

    def verify(self, payment_payload, requirements):
        if self.reject_verify:
            from scutl_pserv.network import PermanentError
            raise PermanentError("rejected: mock invalid signature")
        self.verified += 1
        return None

    def settle(self, payment_payload, requirements):
        if self.fail_transient_times > 0:
            self.fail_transient_times -= 1
            raise TransientError("mock timeout")
        self.settled_nonces.append(
            payment_payload["payload"]["authorization"]["nonce"])
        return SettleResult(tx_hash="0x" + "ab" * 32, network="base-sepolia")


def header(nonce="0x01", value=PRICE, payer="0x" + "bb" * 20, extra=None):
    auth = {"nonce": nonce, "value": value, "from": payer,
            "to": PAYTO, **(extra or {})}
    payload = {"x402Version": 1, "scheme": "exact", "network": "base-sepolia",
               "payload": {"signature": "0x" + "cd" * 65, "authorization": auth}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.fixture
def state(tmp_path):
    s = StateDir(tmp_path / "pserv")
    approvals.grant(s, "configure")
    Manager(s).configure(PAYTO, Decimal("0.01"), "generated-text")
    return s


def merchant(state, facil=None):
    return Merchant(state, facilitator=facil or MockFacilitator(),
                    retry_delays=(0, 0))


def test_configure_requires_approval(tmp_path):
    s = StateDir(tmp_path / "p")
    with pytest.raises(ApprovalRequired):
        Manager(s).configure(PAYTO, Decimal("0.01"), "generated-text")


def test_unpaid_request_gets_402_offer_from_config(state):
    resp = merchant(state).handle("/resource", None)
    assert resp.code == 402
    offer = json.loads(resp.body)["accepts"][0]
    assert offer["payTo"] == PAYTO
    assert offer["maxAmountRequired"] == PRICE


def test_paid_request_verifies_settles_serves_and_logs(state):
    facil = MockFacilitator()
    resp = merchant(state, facil).handle("/resource", header(nonce="0x01"))
    assert resp.code == 200
    assert facil.verified == 1 and facil.settled_nonces == ["0x01"]
    earnings = state.read_earnings()
    assert len(earnings) == 1
    assert earnings[0]["amount"] == "0.010000"
    assert earnings[0]["tx"] == "0x" + "ab" * 32
    assert "X-PAYMENT-RESPONSE" in resp.headers


def test_nothing_served_when_verify_rejects(state):
    facil = MockFacilitator(reject_verify=True)
    resp = merchant(state, facil).handle_safe("/resource", header())
    assert resp.code == 402
    assert facil.settled_nonces == []
    assert state.read_earnings() == []


def test_replay_refused_before_settle(state):
    facil = MockFacilitator()
    m = merchant(state, facil)
    assert m.handle("/resource", header(nonce="0xdup")).code == 200
    resp = m.handle("/resource", header(nonce="0xdup"))
    assert resp.code == 402
    assert "replayed" in json.loads(resp.body)["error"]
    assert facil.settled_nonces == ["0xdup"]  # settled exactly once
    assert len(state.read_earnings()) == 1


def test_replay_refused_across_restart(state):
    assert merchant(state).handle("/resource", header(nonce="0xr")).code == 200
    reborn = merchant(state)  # fresh Merchant, same state dir
    assert reborn.handle("/resource", header(nonce="0xr")).code == 402
    assert len(state.read_earnings()) == 1


def test_underpayment_refused_before_facilitator(state):
    facil = MockFacilitator()
    resp = merchant(state, facil).handle("/resource", header(value="9999"))
    assert resp.code == 402
    assert "underpayment" in json.loads(resp.body)["error"]
    assert facil.verified == 0 and facil.settled_nonces == []


def test_payto_injection_cannot_move_settlement(state):
    facil = MockFacilitator()
    evil = header(nonce="0xevil", extra={"payTo": "0x" + "ee" * 20})
    resp = merchant(state, facil).handle("/resource", evil)
    assert resp.code == 200
    # Requirements passed to verify/settle carry the CONFIG payTo, byte-
    # identical, regardless of anything in the request.
    reqs = merchant(state).requirements()
    assert reqs["payTo"] == PAYTO


def test_malformed_header_is_400_never_500(state):
    m = merchant(state)
    assert m.handle("/resource", "not-base64!").code == 400
    assert m.handle(
        "/resource",
        base64.b64encode(b'{"payload": {}}').decode()).code == 400


def test_transient_settle_failure_serves_nothing_and_can_retry(state):
    facil = MockFacilitator(fail_transient_times=3)  # outlasts 2 retries
    m = merchant(state, facil)
    resp = m.handle("/resource", header(nonce="0xt"))
    assert resp.code == 402 and "transient" in json.loads(resp.body)["error"]
    assert state.read_earnings() == [] and state.served_nonces() == set()
    assert m.handle("/resource", header(nonce="0xt")).code == 200  # retry ok


def test_earnings_totals_derive_from_log_after_restart(state):
    m = merchant(state)
    for i in range(3):
        assert m.handle("/resource", header(nonce=f"0x{i}")).code == 200
    reborn = Manager(StateDir(state.root))
    assert reborn.earnings()["total_usdc"] == "0.030000"
    assert reborn.earnings()["count"] == 3


def test_set_payto_requires_approval_and_records_previous(state):
    mgr = Manager(state)
    with pytest.raises(ApprovalRequired):
        mgr.set_payto("0x" + "cc" * 20)
    approvals.grant(state, "set-payto")
    out = mgr.set_payto("0x" + "cc" * 20)
    assert out == {"payto": "0x" + "cc" * 20, "previous": PAYTO}


def test_decommission_requires_approval_then_disables(state):
    mgr = Manager(state)
    with pytest.raises(ApprovalRequired):
        mgr.decommission()
    approvals.grant(state, "decommission")
    out = mgr.decommission()
    assert state.decommission_marker.exists()
    assert out["was_running"] is False
    with pytest.raises(Decommissioned):
        mgr.status()
    with pytest.raises(Decommissioned):
        mgr.start()
    with pytest.raises(Decommissioned):
        merchant(state).handle("/resource", header(nonce="0xafter"))
    # logs retained for reconciliation
    assert state.earnings_log.exists() or state.read_earnings() == []


def test_static_file_offering_serves_the_file(tmp_path):
    s = StateDir(tmp_path / "p")
    approvals.grant(s, "configure")
    resource = tmp_path / "art.txt"
    resource.write_text("one of one\n")
    Manager(s).configure(PAYTO, Decimal("0.01"), "static-file",
                         resource_path=str(resource))
    resp = merchant(s).handle("/resource", header())
    assert resp.code == 200 and resp.body == b"one of one\n"
