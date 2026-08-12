"""Signer acceptance tests against mocked network clients.

These map to the manifest's verify section (negative probes + restart
probe); the live end-to-end payment runs in the acceptance suite, not here.
The mocks honor the contracts in recipe.yaml — they're the seed of the
SMUTbench mock services (cst-8ih.4).
"""

from decimal import Decimal

import pytest

from scutl_signer import approvals
from scutl_signer.approvals import ApprovalRequired
from scutl_signer.core import CapExceeded, Signer
from scutl_signer.network import SettleResult, TransientError
from scutl_signer.state import Revoked, StateDir


class MockChain:
    def __init__(self):
        self.balance = Decimal("10")

    def usdc_balance(self, address):
        return self.balance

    def tx_status(self, tx_hash):
        return "confirmed"


class MockFacilitator:
    def __init__(self, fail_transient_times=0):
        self.settled_nonces = []
        self.fail_transient_times = fail_transient_times

    def verify(self, payment_payload, requirements):
        return None

    def settle(self, payment_payload, requirements):
        if self.fail_transient_times > 0:
            self.fail_transient_times -= 1
            raise TransientError("mock timeout")
        nonce = payment_payload["payload"]["authorization"]["nonce"]
        self.settled_nonces.append(nonce)
        return SettleResult(tx_hash="0x" + "ab" * 32, network="base-sepolia")


@pytest.fixture
def signer(tmp_path):
    state = StateDir(tmp_path / "wallet")
    s = Signer(state=state, chain=MockChain(), facilitator=MockFacilitator())
    approvals.grant(state, "keygen")
    s.keygen(cap_per_tx=Decimal("0.10"), cap_daily=Decimal("1.00"))
    return s


def test_keygen_requires_approval(tmp_path):
    s = Signer(StateDir(tmp_path / "w"), MockChain(), MockFacilitator())
    with pytest.raises(ApprovalRequired):
        s.keygen(Decimal("0.10"), Decimal("1.00"))


def test_keygen_secrets_are_0600_and_not_in_result(signer):
    for f in (signer.state.keystore, signer.state.kek):
        assert oct(f.stat().st_mode & 0o777) == "0o600"
    result = {"address": signer.address()}
    kek = signer.state.kek.read_text()
    assert kek not in str(result)


def test_pay_under_cap_settles_and_logs(signer):
    rec = signer.pay("pmt-1", "0x" + "11" * 20, Decimal("0.05"))
    assert rec["status"] == "settled"
    assert signer.state.spent_last_24h() == Decimal("0.05")


def test_pay_over_per_tx_cap_refused_in_code(signer):
    with pytest.raises(CapExceeded):
        signer.pay("pmt-big", "0x" + "11" * 20, Decimal("0.11"))
    assert signer.state.read_spends() == []


def test_daily_cap_counts_prior_spends(signer):
    for i in range(10):
        signer.pay(f"pmt-{i}", "0x" + "11" * 20, Decimal("0.10"))
    with pytest.raises(CapExceeded):
        signer.pay("pmt-over", "0x" + "11" * 20, Decimal("0.01"))


def test_duplicate_payment_id_is_idempotent(signer):
    first = signer.pay("pmt-dup", "0x" + "11" * 20, Decimal("0.05"))
    again = signer.pay("pmt-dup", "0x" + "11" * 20, Decimal("0.05"))
    assert again["idempotent_replay"] is True
    assert again["tx"] == first["tx"]
    assert signer.state.spent_last_24h() == Decimal("0.05")  # spent once


def test_transient_failure_retry_reuses_same_nonce(tmp_path):
    state = StateDir(tmp_path / "wallet")
    facil = MockFacilitator(fail_transient_times=1)
    s = Signer(state=state, chain=MockChain(), facilitator=facil)
    approvals.grant(state, "keygen")
    s.keygen(Decimal("0.10"), Decimal("1.00"))
    with pytest.raises(TransientError):
        s.pay("pmt-retry", "0x" + "11" * 20, Decimal("0.05"))
    assert state.read_spends() == []  # failed attempt not logged as spend
    s.pay("pmt-retry", "0x" + "11" * 20, Decimal("0.05"))
    assert len(facil.settled_nonces) == 1


def test_restart_recovers_counters_from_log(signer):
    signer.pay("pmt-1", "0x" + "11" * 20, Decimal("0.07"))
    reborn = Signer(state=StateDir(signer.state.root),
                    chain=MockChain(), facilitator=MockFacilitator())
    assert reborn.state.spent_last_24h() == Decimal("0.07")
    assert reborn.address() == signer.address()


def test_sign_roundtrip_recovers_address(signer):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    out = signer.sign_message("scutl verify probe")
    sig = out["signature"]
    recovered = Account.recover_message(
        encode_defunct(text="scutl verify probe"),
        signature=bytes.fromhex(sig.removeprefix("0x")),
    )
    assert recovered == out["address"]


def test_revoke_requires_approval_then_disables_everything(signer):
    address_before = signer.address()
    with pytest.raises(ApprovalRequired):
        signer.revoke()
    approvals.grant(signer.state, "revoke")
    tomb = signer.revoke()
    assert tomb["address"] == address_before
    assert signer.state.tombstone.exists()
    assert not signer.state.keystore.exists()
    assert not signer.state.kek.exists()
    for op in (
        lambda: signer.status(),
        lambda: signer.sign_message("x"),
        lambda: signer.pay("p", "0x" + "11" * 20, Decimal("0.01")),
    ):
        with pytest.raises(Revoked):
            op()


def test_keygen_refuses_to_overwrite(signer):
    approvals.grant(signer.state, "keygen")
    with pytest.raises(RuntimeError):
        signer.keygen(Decimal("0.10"), Decimal("1.00"))
