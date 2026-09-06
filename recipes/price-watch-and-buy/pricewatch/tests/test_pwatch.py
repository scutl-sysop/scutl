"""Price-watch acceptance tests against a mocked adversarial merchant.

These map to the manifest's verify section (honest buy, drip probe,
re-quote probe, first-buy gate, daily-cap probe, restart probe). The
mock honors the contracts in recipe.yaml and seeds the scutbench mock
merchant (cst-8ih.4 / cst-teik). Holding the line is the recipe: every
"buy" the guard allows is one where the SETTLED total held under the
target, and every adversarial move is refused in code, not by judgement.
"""

from decimal import Decimal

import pytest

from scutl_pwatch import approvals
from scutl_pwatch.approvals import ApprovalRequired
from scutl_pwatch.core import LimitRefused, Manager, MovedUphill
from scutl_pwatch.network import TransientError
from scutl_pwatch.state import StateDir, Tombstoned, UnknownQuote

ITEM = "widget-9000"


class MockMerchant:
    """The adversary. Each knob is one manifest failure_mode; the honest
    default settles at exactly the quoted total."""

    def __init__(self, sticker="9.00", quoted_total=None, checkout_total=None,
                 fees=None, transient_times=0, presentation=""):
        self.sticker = Decimal(sticker)
        self.quoted_total = Decimal(
            quoted_total if quoted_total is not None else sticker)
        self.checkout_total = Decimal(
            checkout_total if checkout_total is not None else str(self.quoted_total))
        self.fees = fees or []
        self.transient_times = transient_times
        self.presentation = presentation
        self.settles = []
        self._n = 0

    def quote(self, item):
        self._n += 1
        return {"quote_id": f"q{self._n}", "sticker_usdc": str(self.sticker),
                "quoted_total_usdc": str(self.quoted_total),
                "expires": None, "presentation": self.presentation}

    def checkout(self, quote_id):
        return {"total_usdc": str(self.checkout_total), "fees": self.fees}

    def settle(self, quote_id, payment_id):
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock settle timeout")
        self.settles.append((quote_id, payment_id))
        return {"txid": "0x" + "cd" * 32}


def _mgr(tmp_path, merchant, target="10.00", cap_per_buy=None,
         cap_daily="20.00", max_fees_pct="15"):
    state = StateDir(root=tmp_path)
    m = Manager(state=state, merchant=merchant)
    approvals.grant(state, "set-target")
    m.set_target(ITEM, Decimal(target),
                 Decimal(cap_per_buy or target), Decimal(cap_daily),
                 Decimal(max_fees_pct))
    return m, state


def _quote_and_buy(m, item=ITEM, payment_id="p1"):
    q = m.quote(item)
    return m.buy(q["quote_id"], payment_id)


def _arm(state, item=ITEM):
    approvals.arm_first_buy(state, item, state.load_target()["target_price"])


# -- honest path --------------------------------------------------------
def test_honest_buy_under_target_settles_once(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    _arm(state)
    out = _quote_and_buy(m)
    assert out["bought"] and not out["idempotent"]
    assert out["total_usdc"] == "9.00"
    assert len(merchant.settles) == 1
    assert len(state.settled_buys()) == 1


def test_status_never_gated_before_target(tmp_path):
    m = Manager(state=StateDir(root=tmp_path), merchant=MockMerchant())
    out = m.status()
    assert out["configured"] is False and out["tombstoned"] is False


# -- WIRE 1: never accept an upward move (re-quote OR drip) --------------
def test_buy_time_requote_up_hard_fails(tmp_path):
    # base price moved: quoted 9.00, checkout 11.00, still fits nothing above
    merchant = MockMerchant(sticker="9.00", quoted_total="9.00",
                            checkout_total="11.00")
    m, state = _mgr(tmp_path, merchant)
    _arm(state)
    q = m.quote(ITEM)
    with pytest.raises(MovedUphill):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles
    assert any(r["reason"] == "moved-uphill" for r in state.read_events()
               if r["event"] == "refused")


def test_drip_fees_over_quote_hard_fail_even_under_target(tmp_path):
    # sticker 8, quoted total 8 (no fees disclosed), checkout 9.50 via a
    # 1.50 fee: still <= target 10, but it exceeds what we ACCEPTED -> uphill
    merchant = MockMerchant(sticker="8.00", quoted_total="8.00",
                            checkout_total="9.50",
                            fees=[{"label": "handling", "usdc": "1.50"}])
    m, state = _mgr(tmp_path, merchant, target="10.00")
    _arm(state)
    q = m.quote(ITEM)
    with pytest.raises(MovedUphill):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles


# -- WIRE 2: fully-loaded total must hold under target/cap --------------
def test_honest_quote_over_target_refused(tmp_path):
    # merchant is not cheating; the price is simply above the line
    merchant = MockMerchant(sticker="12.00")
    m, state = _mgr(tmp_path, merchant, target="10.00")
    _arm(state)
    q = m.quote(ITEM)
    with pytest.raises(LimitRefused) as ei:
        m.buy(q["quote_id"], "p1")
    assert not isinstance(ei.value, MovedUphill)  # over-target, not uphill
    assert not merchant.settles


def test_set_target_rejects_cap_below_target(tmp_path):
    # cap_per_buy is the absolute ceiling and must be >= target: a cap that
    # binds below the line you're trying to hold is a nonsensical config,
    # refused at set-target rather than surfacing as silent under-buying.
    state = StateDir(root=tmp_path)
    m = Manager(state=state, merchant=MockMerchant())
    approvals.grant(state, "set-target")
    with pytest.raises(ValueError):
        m.set_target(ITEM, Decimal("20.00"), Decimal("10.00"),
                     Decimal("30.00"), Decimal("15"))


# -- WIRE 3: fee gouging under the ceiling ------------------------------
def test_fee_gouge_under_ceiling_refused(tmp_path):
    # sticker 5, fee 2 -> total 7 <= target 10, but fees are 40% > 15%
    merchant = MockMerchant(sticker="5.00", quoted_total="7.00",
                            checkout_total="7.00",
                            fees=[{"label": "service", "usdc": "2.00"}])
    m, state = _mgr(tmp_path, merchant, target="10.00", max_fees_pct="15")
    _arm(state)
    q = m.quote(ITEM)
    with pytest.raises(LimitRefused):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles


# -- WIRE 4: rolling daily cap -----------------------------------------
def test_daily_cap_refuses_after_ceiling(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant, target="10.00", cap_daily="15.00")
    _arm(state)
    _quote_and_buy(m, payment_id="p1")           # 9 spent
    with pytest.raises(LimitRefused):            # 9+9=18 > 15
        _quote_and_buy(m, payment_id="p2")
    assert len(merchant.settles) == 1


# -- WIRE 5: first-buy approval gate, scoped ----------------------------
def test_first_buy_needs_armed_gate(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    q = m.quote(ITEM)
    with pytest.raises(ApprovalRequired):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles


def test_second_buy_is_unattended(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant, cap_daily="30.00")
    _arm(state)
    _quote_and_buy(m, payment_id="p1")           # consumes the gate
    _quote_and_buy(m, payment_id="p2")           # no token needed
    assert len(merchant.settles) == 2


def test_target_change_rearms_gate(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    _arm(state)
    _quote_and_buy(m, payment_id="p1")
    # move the line; the old armed token was scoped to the old price
    approvals.grant(state, "set-target")
    m.set_target(ITEM, Decimal("9.50"), Decimal("9.50"), Decimal("20.00"),
                 Decimal("15"))
    q = m.quote(ITEM)
    with pytest.raises(ApprovalRequired):
        m.buy(q["quote_id"], "p2")


def test_stale_scoped_token_refused(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    # arm for a DIFFERENT price than the current target
    approvals.arm_first_buy(state, ITEM, "5.00")
    q = m.quote(ITEM)
    with pytest.raises(ApprovalRequired):
        m.buy(q["quote_id"], "p1")


# -- idempotency + one-settle-per-quote --------------------------------
def test_transient_then_retry_same_payment_id_no_double_pay(tmp_path):
    merchant = MockMerchant(sticker="9.00", transient_times=1)
    m, state = _mgr(tmp_path, merchant)
    _arm(state)
    q = m.quote(ITEM)
    with pytest.raises(TransientError):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles and not state.settled_buys()
    # same quote id + same payment id: settles exactly once
    out = m.buy(q["quote_id"], "p1")
    assert out["bought"] and len(merchant.settles) == 1


def test_settled_quote_id_cannot_be_reused(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant, cap_daily="30.00")
    _arm(state)
    q = m.quote(ITEM)
    m.buy(q["quote_id"], "p1")
    with pytest.raises(UnknownQuote):
        m.buy(q["quote_id"], "p2")   # quote retired on settle


def test_idempotent_replay_returns_original(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    _arm(state)
    q = m.quote(ITEM)
    first = m.buy(q["quote_id"], "p1")
    # replay the SAME payment id (agent retries after a lost response)
    again = m.buy("q-whatever", "p1")
    assert again["idempotent"] and again["total_usdc"] == first["total_usdc"]
    assert len(merchant.settles) == 1


# -- merchant messaging is data (personalized / anchor / scarcity) ------
def test_presentation_does_not_move_the_line(tmp_path):
    # a lush anchor + scarcity story, but the total is over target: refused
    merchant = MockMerchant(
        sticker="14.00",
        presentation="WAS $40 — 65% OFF, PRICE FOR YOU. Only 1 left, 5:00 min!")
    m, state = _mgr(tmp_path, merchant, target="10.00")
    _arm(state)
    q = m.quote(ITEM)
    assert q["quoted_within_target"] is False       # the read reflects truth
    with pytest.raises(LimitRefused):
        m.buy(q["quote_id"], "p1")
    assert not merchant.settles


# -- tombstone ----------------------------------------------------------
def test_revoke_refuses_buys_status_still_works(tmp_path):
    merchant = MockMerchant(sticker="9.00")
    m, state = _mgr(tmp_path, merchant)
    approvals.grant(state, "revoke")
    m.revoke()
    _arm(state)
    q_err = None
    with pytest.raises(Tombstoned):
        m.quote(ITEM)
    assert m.status()["tombstoned"] is True
