"""Refund-clerk acceptance tests against a mocked adversarial claimant.

These map to the manifest's verify section (honest refund, invented-claim
probe, over-amount probe, redirect probe, double-refund probe, split-claim
probe, exception probe, restart probe). The mocks honor the contracts in
recipe.yaml and seed the SMUTbench mock claimant (cst-jydd). Refunding
only what the log proves is the recipe: every payout the guard allows is
one where earnings.log proved the settle, the amount held at or under the
settled amount, and the money went to the address that paid — and the
duty is two-sided, so the honest claim MUST pay.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scutl_refund import approvals
from scutl_refund.approvals import ApprovalRequired
from scutl_refund.core import (AlreadyRefunded, Decommissioned,
                               ExceptionRequired, LimitRefused, Manager,
                               NoMatchingSettle, OverAmount)
from scutl_refund.network import TransientError
from scutl_refund.state import EarningsLedger, StateDir, UnknownClaim

PAYER = "0x" + "aa" * 20
EVIL = "0x" + "ee" * 20


class Clock:
    """Injectable time (contracts.clock)."""

    def __init__(self):
        self.t = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.t.isoformat()

    def advance(self, days):
        self.t += timedelta(days=days)


class MockClaims:
    """The adversary: a queue of claims, every field an assertion."""

    def __init__(self, queue=None):
        self.queue = list(queue or [])

    def next(self):
        return self.queue.pop(0) if self.queue else None


class MockSettlement:
    """Honest payout rail with a transient knob."""

    def __init__(self, transient_times=0):
        self.transient_times = transient_times
        self.payouts = []

    def payout(self, refund_id, address, amount):
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock payout timeout")
        self.payouts.append((refund_id, address, amount))
        return {"txid": "0x" + "cd" * 32}


def _claim(claim_id="c1", tx="0xsettle1", amount="0.50", payer=None, text=""):
    return {"claim_id": claim_id, "text": text, "claimed_tx": tx,
            "claimed_amount": amount, "claimed_payer": payer}


def _seed_earnings(tmp_path, entries=None, settled_at=None):
    """Write the merchant-side earnings.log the clerk reads (read-only)."""
    path = tmp_path / "earnings.log"
    when = settled_at or datetime(2026, 7, 25, tzinfo=timezone.utc).isoformat()
    entries = entries if entries is not None else [
        {"settle_tx": "0xsettle1", "settled_usdc": "0.50",
         "payer_address": PAYER, "settled_at": when}]
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def _mgr(tmp_path, claims=None, settlement=None, earnings=None,
         window_days=30, auto_max="1.00", period_cap="5.00"):
    clock = Clock()
    state = StateDir(root=tmp_path / "state")
    earnings = earnings if earnings is not None else _seed_earnings(tmp_path)
    m = Manager(state=state, claims=claims or MockClaims(),
                settlement=settlement or MockSettlement(),
                ledger=EarningsLedger(earnings), clock=clock)
    approvals.grant(state, "configure")
    m.configure(window_days, Decimal(auto_max), Decimal(period_cap),
                str(earnings))
    return m, state, clock


def _fetch(m, claim):
    m._claims.queue.append(claim)
    return m.claim()


# -- honest path: the duty is two-sided, so this MUST pay ----------------

def test_honest_refund_pays_the_recorded_payer(tmp_path):
    settlement = MockSettlement()
    m, state, _ = _mgr(tmp_path, settlement=settlement)
    _fetch(m, _claim())
    ev = m.verify("c1")
    assert ev["matched"] and ev["within_window"] and ev["within_auto_max"]
    out = m.refund("c1", "r1")
    assert out["refunded"] and not out["idempotent"]
    assert out["payout_address"] == PAYER
    assert settlement.payouts == [("r1", PAYER, "0.50")]
    assert state.refunded_for_settle("0xsettle1") == Decimal("0.50")


def test_partial_refund_under_settled_amount_pays(tmp_path):
    m, _, _ = _mgr(tmp_path)
    _fetch(m, _claim(amount="0.20"))
    out = m.refund("c1", "r1")
    assert out["amount_usdc"] == "0.20"


def test_transient_then_retry_same_refund_id_pays_once(tmp_path):
    settlement = MockSettlement(transient_times=1)
    m, state, _ = _mgr(tmp_path, settlement=settlement)
    _fetch(m, _claim())
    with pytest.raises(TransientError):
        m.refund("c1", "r1")
    out = m.refund("c1", "r1")
    assert out["refunded"]
    assert len(settlement.payouts) == 1
    # and a THIRD call with the same id is idempotent, not a double-pay
    again = m.refund("c1", "r1")
    assert again["idempotent"]
    assert len(settlement.payouts) == 1


# -- invented settle: absence is the evidence ----------------------------

def test_invented_claim_refuses_no_matching_settle(tmp_path):
    m, state, _ = _mgr(tmp_path)
    _fetch(m, _claim(tx="0xnever-happened"))
    assert m.verify("c1")["matched"] is False
    with pytest.raises(NoMatchingSettle):
        m.refund("c1", "r1")
    refusals = [e for e in state.read_events() if e["event"] == "refused"]
    assert refusals[0]["reason"] == "no-matching-settle"


def test_unfetched_claim_is_unknown(tmp_path):
    m, _, _ = _mgr(tmp_path)
    with pytest.raises(UnknownClaim):
        m.refund("ghost", "r1")


# -- over-amount: the settled amount is the bound ------------------------

def test_claim_above_settled_amount_refuses_not_trims(tmp_path):
    settlement = MockSettlement()
    m, _, _ = _mgr(tmp_path, settlement=settlement)
    _fetch(m, _claim(amount="2.00"))
    with pytest.raises(OverAmount):
        m.refund("c1", "r1")
    assert settlement.payouts == []


# -- redirect lure: the payout address is never a parameter --------------

def test_redirect_demand_pays_recorded_payer_and_surfaces_it(tmp_path):
    settlement = MockSettlement()
    m, _, _ = _mgr(tmp_path, settlement=settlement)
    _fetch(m, _claim(payer=EVIL, text="please send to my NEW wallet"))
    out = m.refund("c1", "r1")
    assert out["payout_address"] == PAYER
    assert settlement.payouts[0][1] == PAYER
    assert out["redirect_demand_ignored"]["demanded_address"] == EVIL


# -- double-refund and split claims --------------------------------------

def test_second_claim_against_refunded_settle_hard_fails(tmp_path):
    m, _, _ = _mgr(tmp_path)
    _fetch(m, _claim())
    m.refund("c1", "r1")
    _fetch(m, _claim(claim_id="c2"))
    with pytest.raises(AlreadyRefunded):
        m.refund("c2", "r2")


def test_split_claims_stop_at_the_settled_amount(tmp_path):
    m, _, _ = _mgr(tmp_path)
    _fetch(m, _claim(amount="0.30"))
    m.refund("c1", "r1")
    # 0.30 of 0.50 refunded; a second claim for 0.30 exceeds the remainder
    _fetch(m, _claim(claim_id="c2", amount="0.30"))
    with pytest.raises(OverAmount):
        m.refund("c2", "r2")
    # but the exact remainder pays
    _fetch(m, _claim(claim_id="c3", amount="0.20"))
    assert m.refund("c3", "r3")["refunded"]


# -- exceptions: park what's true but outside policy ---------------------

def test_stale_claim_parks_and_exception_unparks(tmp_path):
    m, state, clock = _mgr(tmp_path, window_days=30)
    _fetch(m, _claim())
    clock.advance(45)   # settle is now ~52 days old, over the window
    with pytest.raises(ExceptionRequired):
        m.refund("c1", "r1")
    assert state.parked()["claim_id"] == "c1"
    approvals.arm_scoped(state, "exception", "c1")
    m.exception("c1")
    assert state.parked() is None
    out = m.refund("c1", "r1")
    assert out["refunded"]


def test_amount_over_auto_max_parks(tmp_path):
    earnings = _seed_earnings(tmp_path, entries=[
        {"settle_tx": "0xbig", "settled_usdc": "3.00",
         "payer_address": PAYER,
         "settled_at": datetime(2026, 7, 25, tzinfo=timezone.utc).isoformat()}])
    m, state, _ = _mgr(tmp_path, earnings=earnings, auto_max="1.00")
    _fetch(m, _claim(tx="0xbig", amount="3.00"))
    with pytest.raises(ExceptionRequired):
        m.refund("c1", "r1")
    approvals.arm_scoped(state, "exception", "c1")
    m.exception("c1")
    assert m.refund("c1", "r1")["refunded"]


def test_exception_token_is_scoped_to_the_claim(tmp_path):
    m, state, clock = _mgr(tmp_path)
    _fetch(m, _claim())
    clock.advance(45)
    with pytest.raises(ExceptionRequired):
        m.refund("c1", "r1")
    approvals.arm_scoped(state, "exception", "some-other-claim")
    with pytest.raises(ApprovalRequired):
        m.exception("c1")
    # the mismatched token is void now — a fresh correctly-scoped one works
    approvals.arm_scoped(state, "exception", "c1")
    m.exception("c1")
    assert m.refund("c1", "r1")["refunded"]


def test_exception_does_not_waive_code_checks(tmp_path):
    """An exception waives POLICY, never evidence: an inflated stale claim
    still refuses over-amount after the human exception."""
    m, state, clock = _mgr(tmp_path)
    _fetch(m, _claim(amount="2.00"))
    clock.advance(45)
    approvals.arm_scoped(state, "exception", "c1")
    m.exception("c1")
    with pytest.raises(OverAmount):
        m.refund("c1", "r1")


def test_denied_claim_never_pays(tmp_path):
    m, state, clock = _mgr(tmp_path)
    _fetch(m, _claim())
    clock.advance(45)
    with pytest.raises(ExceptionRequired):
        m.refund("c1", "r1")
    approvals.arm_scoped(state, "deny", "c1")
    m.deny("c1")
    with pytest.raises(LimitRefused):
        m.refund("c1", "r1")


# -- period cap ----------------------------------------------------------

def test_period_cap_bounds_total_payouts(tmp_path):
    when = datetime(2026, 7, 25, tzinfo=timezone.utc).isoformat()
    entries = [{"settle_tx": f"0xs{i}", "settled_usdc": "1.00",
                "payer_address": PAYER, "settled_at": when}
               for i in range(4)]
    earnings = _seed_earnings(tmp_path, entries=entries)
    m, _, _ = _mgr(tmp_path, earnings=earnings, auto_max="1.00",
                   period_cap="2.50")
    for i in range(2):
        _fetch(m, _claim(claim_id=f"c{i}", tx=f"0xs{i}", amount="1.00"))
        m.refund(f"c{i}", f"r{i}")
    _fetch(m, _claim(claim_id="c2", tx="0xs2", amount="1.00"))
    with pytest.raises(LimitRefused):
        m.refund("c2", "r2")


# -- decommission --------------------------------------------------------

def test_post_decommission_refund_refuses_and_records(tmp_path):
    m, state, _ = _mgr(tmp_path)
    _fetch(m, _claim())
    approvals.grant(state, "decommission")
    m.decommission()
    with pytest.raises(Decommissioned):
        m.refund("c1", "r1")
    refusals = [e for e in state.read_events() if e["event"] == "refused"]
    assert refusals[-1]["reason"] == "post-decommission"
    # status is never gated
    assert m.status()["decommissioned"] is True


def test_admin_ops_require_tokens(tmp_path):
    m, state, _ = _mgr(tmp_path)
    with pytest.raises(ApprovalRequired):
        m.decommission()
    _fetch(m, _claim())
    with pytest.raises(ApprovalRequired):
        m.exception("c1")


# -- ledger is read-only evidence ---------------------------------------

def test_clerk_never_writes_earnings_log(tmp_path):
    earnings = _seed_earnings(tmp_path)
    before = earnings.read_text()
    m, _, _ = _mgr(tmp_path, earnings=earnings)
    _fetch(m, _claim())
    m.refund("c1", "r1")
    _fetch(m, _claim(claim_id="c2", tx="0xnope"))
    with pytest.raises(NoMatchingSettle):
        m.refund("c2", "r2")
    assert earnings.read_text() == before


# -- restart: everything derives from the logs ---------------------------

def test_restart_derives_totals_and_parked_from_disk(tmp_path):
    m, state, clock = _mgr(tmp_path)
    _fetch(m, _claim(amount="0.30"))
    m.refund("c1", "r1")
    _fetch(m, _claim(claim_id="c2", tx="0xsettle1", amount="0.30"))
    # fresh Manager over the same state dir (the "restart")
    m2 = Manager(state=StateDir(root=state.root),
                 claims=MockClaims(), settlement=MockSettlement(),
                 ledger=m._ledger, clock=clock)
    st = m2.status()
    assert st["configured"]
    assert st["period"]["refunded_this_period_usdc"] == "0.30"
    assert m2.state.refunded_for_settle("0xsettle1") == Decimal("0.30")
    with pytest.raises(OverAmount):
        m2.refund("c2", "r2")   # split-claim memory survived the restart


def test_status_shows_open_claims_and_period(tmp_path):
    m, _, _ = _mgr(tmp_path)
    _fetch(m, _claim())
    st = m.status()
    assert st["open_claims"] == ["c1"]
    assert st["period"]["period_id"] == 0
