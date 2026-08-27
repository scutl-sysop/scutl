"""Acceptance tests for the mwallet custody layer (recipe #1 rev 1).

Everything runs against a tmp state root with an injected mock chain,
mock facilitator, and — the recipe's time axis — an injectable clock.
The inner signer's own behavior (caps under lock, idempotency, EIP-3009
shape) is covered by wallet-base-sepolia's suite; here we test what the
custody shell ADDS and that its gates wrap every spend path.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from scutl_signer.core import CapExceeded
from scutl_signer.state import Revoked

from scutl_mwallet import approvals
from scutl_mwallet.approvals import ApprovalRequired
from scutl_mwallet.core import Custodian
from scutl_mwallet.custody import CeremonyIncomplete, Panicked

class FakeClock:
    """Starts at REAL now: the inner signer stamps spend records with the
    wall clock (it takes no clock injection — a documented rev-1 seam),
    so the custody clock must begin in the same neighborhood and only
    ever diverge by explicit advance()."""

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class MockChain:
    def __init__(self, balance: str = "0"):
        self.balance = Decimal(balance)

    def usdc_balance(self, address: str) -> Decimal:
        return self.balance

    def tx_status(self, tx_hash: str) -> str:
        return "confirmed"


class MockFacilitator:
    def __init__(self):
        self.settles = []

    def verify(self, payload, requirements) -> None:
        pass

    def settle(self, payload, requirements):
        self.settles.append(payload)
        return SimpleNamespace(tx_hash="0x" + "ab" * 32)


@pytest.fixture
def cust(tmp_path):
    def make(balance="10", clock=None):
        return Custodian(state_root=tmp_path / "state",
                         chain=MockChain(balance),
                         facilitator=MockFacilitator(),
                         clock=clock or FakeClock())
    return make


def full_ceremony(c: Custodian, tmp_path: Path,
                  caps=("0.25", "1.00", "5.00"), delay="24") -> Path:
    """Run keygen + backup + backup-verify + restore-rehearsal; returns
    the backup dir (the human's offline copy, simulated)."""
    approvals.grant(c.wstate, "keygen")
    c.keygen(Decimal(caps[0]), Decimal(caps[1]), Decimal(caps[2]),
             Decimal(delay))
    backup = tmp_path / "offline-backup"
    backup.mkdir(exist_ok=True)
    shutil.copyfile(c.wstate.keystore, backup / "keystore.json")
    shutil.copyfile(c.wstate.kek, backup / "kek")
    approvals.grant(c.wstate, "backup-verify")
    c.backup_verify()
    approvals.grant(c.wstate, "restore-rehearsal")
    c.restore_rehearsal(backup)
    return backup


# -- ceremony gating ------------------------------------------------------

def test_spend_refused_before_any_ceremony(cust):
    c = cust()
    with pytest.raises(CeremonyIncomplete):
        c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))


def test_spend_refused_after_keygen_only(cust):
    c = cust()
    approvals.grant(c.wstate, "keygen")
    c.keygen(Decimal("0.25"), Decimal("1"), Decimal("5"), Decimal("24"))
    with pytest.raises(CeremonyIncomplete) as e:
        c.authorize("p1", "0x" + "11" * 20, Decimal("0.01"))
    assert "backup" in str(e.value)


def test_spend_refused_missing_only_rehearsal(cust, tmp_path):
    c = cust()
    approvals.grant(c.wstate, "keygen")
    c.keygen(Decimal("0.25"), Decimal("1"), Decimal("5"), Decimal("24"))
    approvals.grant(c.wstate, "backup-verify")
    c.backup_verify()
    with pytest.raises(CeremonyIncomplete) as e:
        c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))
    assert "restore_rehearsal" in str(e.value)


def test_full_ceremony_unlocks_spend(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    out = c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))
    assert out["status"] == "settled"


def test_rehearsal_requires_matching_backup(cust, tmp_path):
    c = cust()
    approvals.grant(c.wstate, "keygen")
    c.keygen(Decimal("0.25"), Decimal("1"), Decimal("5"), Decimal("24"))
    approvals.grant(c.wstate, "backup-verify")
    c.backup_verify()
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    (bogus / "keystore.json").write_text("{}")
    (bogus / "kek").write_text("nope")
    approvals.grant(c.wstate, "restore-rehearsal")
    with pytest.raises(ValueError, match="fingerprint"):
        c.restore_rehearsal(bogus)
    assert not c.status()["ceremony"]["restore_rehearsal"]


def test_rehearsal_is_gated(cust, tmp_path):
    c = cust()
    approvals.grant(c.wstate, "keygen")
    c.keygen(Decimal("0.25"), Decimal("1"), Decimal("5"), Decimal("24"))
    with pytest.raises(ApprovalRequired):
        c.restore_rehearsal(tmp_path)


def test_keygen_requires_nested_caps(cust):
    c = cust()
    approvals.grant(c.wstate, "keygen")
    with pytest.raises(ValueError, match="nest"):
        c.keygen(Decimal("2"), Decimal("1"), Decimal("5"), Decimal("24"))


# -- cap tiers ------------------------------------------------------------

def test_per_tx_and_daily_still_enforced(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    with pytest.raises(CapExceeded):
        c.pay("big", "0x" + "11" * 20, Decimal("0.30"))     # > per-tx 0.25


def test_lifetime_cap_blocks_when_daily_would_allow(cust, tmp_path):
    """Lifetime binds independently of the daily cap. (The sharper form —
    daily resets after 24h while lifetime never does — needs the INNER
    clock mocked, which the rev-1 signer doesn't expose; that scenario is
    the bench's, via the clock contract.)"""
    c = cust()
    full_ceremony(c, tmp_path, caps=("0.25", "1.00", "1.10"))
    to = "0x" + "11" * 20
    for i in range(4):                         # 4 x 0.25 = 1.00, daily-legal
        c.pay(f"p{i}", to, Decimal("0.25"))
    with pytest.raises(CapExceeded, match="cap_lifetime"):
        c.pay("p5", to, Decimal("0.15"))       # 1.00 + 0.15 > lifetime 1.10


def test_lifetime_counts_outstanding_authorizations(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path, caps=("0.25", "0.40", "0.40"))
    to = "0x" + "11" * 20
    c.authorize("a1", to, Decimal("0.25"))     # reserved, not settled
    with pytest.raises(CapExceeded, match="cap_lifetime"):
        c.authorize("a2", to, Decimal("0.20"))  # 0.25 + 0.20 > 0.40


def test_lifetime_replay_does_not_double_count(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path, caps=("0.25", "0.30", "0.30"))
    to = "0x" + "11" * 20
    c.authorize("a1", to, Decimal("0.25"))
    out = c.authorize("a1", to, Decimal("0.25"))   # same payment_id replay
    assert out["payment_id"] == "a1"


# -- ratchet --------------------------------------------------------------

def test_ratchet_raise_queues_and_matures(cust, tmp_path):
    clock = FakeClock()
    c = cust(clock=clock)
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.50")
    out = c.ratchet("cap_per_tx", Decimal("0.50"))
    assert out["immediate"] is False
    # not yet effective: still the old cap
    with pytest.raises(CapExceeded):
        c.pay("p1", "0x" + "11" * 20, Decimal("0.40"))
    clock.advance(hours=25)
    out = c.pay("p1", "0x" + "11" * 20, Decimal("0.40"))
    assert out["status"] == "settled"
    assert c.status()["caps"]["per_tx"] == "0.50"
    assert c.status()["pending_ratchets"] == []


def test_ratchet_lower_is_immediate_and_cancels_pending_raise(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.50")
    c.ratchet("cap_per_tx", Decimal("0.50"))          # pending raise
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.10")
    out = c.ratchet("cap_per_tx", Decimal("0.10"))    # lower: now
    assert out["immediate"] is True
    assert c.status()["caps"]["per_tx"] == "0.10"
    assert c.status()["pending_ratchets"] == []       # raise cancelled
    with pytest.raises(CapExceeded):
        c.pay("p1", "0x" + "11" * 20, Decimal("0.20"))


def test_ratchet_token_scope_mismatch_refuses(cust, tmp_path):
    c = cust(clock=FakeClock())
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.50")
    with pytest.raises(ApprovalRequired):             # different number
        c.ratchet("cap_per_tx", Decimal("2.00"))
    with pytest.raises(ApprovalRequired):             # token was consumed
        c.ratchet("cap_per_tx", Decimal("0.50"))
    assert c.status()["pending_ratchets"] == []


def test_ratchet_requires_token_at_all(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    with pytest.raises(ApprovalRequired):
        c.ratchet("cap_daily", Decimal("2.00"))


def test_ratchet_lifetime_cap(cust, tmp_path):
    clock = FakeClock()
    c = cust(clock=clock)
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_lifetime", "10.00")
    c.ratchet("cap_lifetime", Decimal("10.00"))
    clock.advance(hours=25)
    c.status()                                        # lazy apply
    assert c.status()["caps"]["lifetime"] == "10.00"


def test_rolled_back_clock_does_not_apply_raises(cust, tmp_path):
    clock = FakeClock()
    c = cust(clock=clock)
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.50")
    c.ratchet("cap_per_tx", Decimal("0.50"))
    clock.advance(hours=2)
    c.status()                                        # advance high-water
    clock.now -= timedelta(hours=48)                  # roll WAY back
    # even though (rolled-back now + nothing) — effective_at is absolute,
    # but also the anomaly path must keep the raise pending
    c.status()
    assert c.status()["caps"]["per_tx"] == "0.25"
    assert len(c.status()["pending_ratchets"]) == 1


def test_short_delay_for_live_verify(cust, tmp_path):
    """The manifest's live verify runs delay=0.05h (3 min) — prove a
    fractional-hour delay works end to end."""
    clock = FakeClock()
    c = cust(clock=clock)
    full_ceremony(c, tmp_path, delay="0.05")
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.30")
    c.ratchet("cap_per_tx", Decimal("0.30"))
    clock.advance(minutes=4)
    c.status()
    assert c.status()["caps"]["per_tx"] == "0.30"


# -- panic ----------------------------------------------------------------

def test_panic_needs_no_approval_and_freezes_spend(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    c.panic("address swap suspected")                 # no token granted
    with pytest.raises(Panicked):
        c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))
    with pytest.raises(Panicked):
        c.ratchet("cap_per_tx", Decimal("0.50"))
    with pytest.raises(Panicked):
        c.sweep("0x" + "22" * 20)
    st = c.status()                                   # status still works
    assert st["panic"]["reason"] == "address swap suspected"


def test_panic_works_before_ceremony(cust):
    c = cust()
    out = c.panic("early alarm")
    assert out["reason"] == "early alarm"


def test_unpanic_is_gated_and_restores(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    c.panic("drill")
    with pytest.raises(ApprovalRequired):
        c.unpanic()
    approvals.grant(c.wstate, "unpanic")
    out = c.unpanic()
    assert out["was"]["reason"] == "drill"
    assert c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))["status"] == "settled"


# -- sweep ----------------------------------------------------------------

def test_sweep_micro_then_remainder(cust, tmp_path):
    c = cust(balance="3.50")
    full_ceremony(c, tmp_path)
    dest = "0x" + "22" * 20
    approvals.grant_sweep(c.wstate, dest, remainder=False)
    micro = c.sweep(dest)
    assert micro["phase"] == "micro"
    assert Decimal(micro["amount"]) == Decimal("0.10")
    assert "header" in micro and micro["header"]
    # remainder refuses without a FRESH token
    with pytest.raises(ApprovalRequired):
        c.sweep(dest, remainder=True)
    approvals.grant_sweep(c.wstate, dest, remainder=True)
    rem = c.sweep(dest, remainder=True)
    assert rem["phase"] == "remainder"
    assert Decimal(rem["amount"]) == Decimal("3.50")


def test_sweep_destination_pinned_by_token(cust, tmp_path):
    c = cust(balance="1")
    full_ceremony(c, tmp_path)
    approvals.grant_sweep(c.wstate, "0x" + "22" * 20, remainder=False)
    with pytest.raises(ApprovalRequired):             # swapped address
        c.sweep("0x" + "33" * 20)


def test_sweep_remainder_requires_same_destination_micro(cust, tmp_path):
    c = cust(balance="1")
    full_ceremony(c, tmp_path)
    other = "0x" + "33" * 20
    approvals.grant_sweep(c.wstate, other, remainder=True)
    with pytest.raises(ValueError, match="micro"):
        c.sweep(other, remainder=True)                # no micro ever issued


def test_sweep_bypasses_spend_caps_but_not_lifetime_accounting(cust, tmp_path):
    c = cust(balance="4.00")
    full_ceremony(c, tmp_path, caps=("0.25", "1.00", "5.00"))
    dest = "0x" + "22" * 20
    approvals.grant_sweep(c.wstate, dest, remainder=False)
    c.sweep(dest)
    approvals.grant_sweep(c.wstate, dest, remainder=True)
    rem = c.sweep(dest, remainder=True)               # 4.00 >> per-tx cap
    assert Decimal(rem["amount"]) == Decimal("4.00")
    # sweep records are excluded from lifetime spend: agent budget intact
    assert Decimal(c.status()["spent_lifetime"]) == Decimal("0")


def test_sweep_requires_ceremony(cust):
    c = cust(balance="1")
    approvals.grant_sweep(c.wstate, "0x" + "22" * 20, remainder=False)
    with pytest.raises(CeremonyIncomplete):
        c.sweep("0x" + "22" * 20)


# -- restart / derivation -------------------------------------------------

def test_state_survives_restart(cust, tmp_path):
    clock = FakeClock()
    c = cust(clock=clock)
    full_ceremony(c, tmp_path)
    approvals.grant_ratchet(c.wstate, "cap_per_tx", "0.50")
    c.ratchet("cap_per_tx", Decimal("0.50"))
    c.pay("p1", "0x" + "11" * 20, Decimal("0.20"))
    clock2 = FakeClock(clock.now + timedelta(minutes=5))
    c2 = cust(clock=clock2)                           # fresh process
    st = c2.status()
    assert st["ceremony"]["complete"]
    assert len(st["pending_ratchets"]) == 1
    assert Decimal(st["spent_lifetime"]) == Decimal("0.20")


def test_revoke_gated_and_terminal(cust, tmp_path):
    c = cust()
    full_ceremony(c, tmp_path)
    with pytest.raises(Exception):                    # inner ApprovalRequired
        c.revoke()
    approvals.grant(c.wstate, "revoke")
    c.revoke()
    with pytest.raises(Revoked):
        c.pay("p1", "0x" + "11" * 20, Decimal("0.01"))
    assert c.status()["tombstoned"] is True
