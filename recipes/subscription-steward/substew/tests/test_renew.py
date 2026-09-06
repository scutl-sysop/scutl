"""Subscription-steward acceptance tests against a mocked adversarial merchant.

These map to the manifest's verify section (honest renewal, hike probe,
drip probe, double-billing probe, cancel probe, consent-scope probe,
restart probe). The mock honors the contracts in recipe.yaml and seeds
the scutbench mock merchant (cst-ydb0). Holding the consented line over
time is the recipe: every renewal the guard allows is one where the
SETTLED total held at or under the price the human agreed to, an upward
re-quote parks for fresh consent instead of settling, and a cancel is
done only when the log proves billing stopped.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scutl_renew import approvals
from scutl_renew.approvals import ApprovalRequired
from scutl_renew.core import (Cancelled, DoubleBilling, LimitRefused,
                              Manager, MovedUphill, ReConsentRequired)
from scutl_renew.network import TransientError
from scutl_renew.state import StateDir, Tombstoned, UnknownQuote, period_of

SERVICE = "cloudbox-pro"


class Clock:
    """Injectable time (contracts.clock): the time axis is the recipe."""

    def __init__(self):
        self.t = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.t.isoformat()

    def advance(self, days):
        self.t += timedelta(days=days)


class MockMerchant:
    """The adversary. Each knob is one manifest failure_mode; the honest
    default settles at exactly the quoted total."""

    def __init__(self, base="8.00", quoted_total=None, renew_total=None,
                 fees=None, transient_times=0, presentation="",
                 cancel_honored=True):
        self.base = Decimal(base)
        self.quoted_total = Decimal(
            quoted_total if quoted_total is not None else base)
        self.renew_total_usdc = Decimal(
            renew_total if renew_total is not None else str(self.quoted_total))
        self.fees = fees or []
        self.transient_times = transient_times
        self.presentation = presentation
        self.cancel_honored = cancel_honored
        self.settles = []
        self._n = 0

    def quote(self):
        self._n += 1
        return {"quote_id": f"q{self._n}", "base_usdc": str(self.base),
                "quoted_total_usdc": str(self.quoted_total),
                "period": "30d", "expires": None,
                "presentation": self.presentation}

    def renew_total(self, quote_id):
        return {"total_usdc": str(self.renew_total_usdc), "fees": self.fees}

    def settle(self, quote_id, payment_id):
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock settle timeout")
        self.settles.append((quote_id, payment_id))
        return {"txid": "0x" + "ab" * 32}

    def cancel(self):
        return {"cancelled": True, "effective": "end-of-period"}


def _mgr(tmp_path, merchant, price="8.00", cap_per_renewal="10.00",
         cap_period="12.00", max_fees_pct="15", period_days=30):
    clock = Clock()
    state = StateDir(root=tmp_path)
    m = Manager(state=state, merchant=merchant, clock=clock)
    approvals.grant(state, "consent")
    m.consent(SERVICE, Decimal(price), period_days,
              Decimal(cap_per_renewal), Decimal(cap_period),
              Decimal(max_fees_pct))
    return m, state, clock


def _quote_and_renew(m, payment_id="p1"):
    q = m.quote()
    return m.renew(q["quote_id"], payment_id)


# -- honest renewal ------------------------------------------------------

def test_honest_renewal_settles_once_and_logs(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    out = _quote_and_renew(m)
    assert out["renewed"] and not out["idempotent"]
    assert out["total_usdc"] == "8.00"
    assert len(merchant.settles) == 1
    log = state.settled_renewals()
    assert len(log) == 1 and log[0]["period_id"] == 0


def test_renewal_below_consented_price_settles(tmp_path):
    # paying LESS than consented is inside the line — the asymmetry is the recipe
    merchant = MockMerchant(base="6.50")
    m, state, clock = _mgr(tmp_path, merchant)
    out = _quote_and_renew(m)
    assert out["renewed"] and out["total_usdc"] == "6.50"


def test_next_period_renews_again(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    _quote_and_renew(m, "p1")
    clock.advance(31)
    out = _quote_and_renew(m, "p2")
    assert out["renewed"] and out["period_id"] == 1
    assert len(merchant.settles) == 2


# -- the hike parks for re-consent (the namesake wire) -------------------

def test_hike_parks_and_does_not_settle(tmp_path):
    merchant = MockMerchant(base="11.00", quoted_total="11.00")
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    with pytest.raises(ReConsentRequired):
        m.renew(q["quote_id"], "p1")
    assert merchant.settles == []
    parked = state.parked()
    assert parked and parked["quoted_total_usdc"] == "11.00"
    assert state.read_events()[-1]["reason"] == "hike-parked"


def test_re_consent_unparks_and_renewal_settles_at_new_price(tmp_path):
    merchant = MockMerchant(base="11.00", quoted_total="11.00")
    m, state, clock = _mgr(tmp_path, merchant, cap_per_renewal="12.00")
    q = m.quote()
    with pytest.raises(ReConsentRequired):
        m.renew(q["quote_id"], "p1")
    approvals.arm_re_consent(state, "11.00")
    out = m.re_consent(Decimal("11.00"))
    assert out["re_consented"] and out["agreed_price"] == "11.00"
    assert state.parked() is None
    out = _quote_and_renew(m, "p2")
    assert out["renewed"] and out["total_usdc"] == "11.00"


def test_re_consent_token_is_scoped_to_the_price(tmp_path):
    merchant = MockMerchant(base="11.00", quoted_total="11.00")
    m, state, clock = _mgr(tmp_path, merchant, cap_per_renewal="12.00")
    approvals.arm_re_consent(state, "10.50")   # human approved a smaller hike
    with pytest.raises(ApprovalRequired):
        m.re_consent(Decimal("11.00"))
    # and the mismatched token was cleared, not left lying around
    with pytest.raises(ApprovalRequired):
        m.re_consent(Decimal("10.50"))


def test_re_consent_without_token_refuses(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    with pytest.raises(ApprovalRequired):
        m.re_consent(Decimal("11.00"))


def test_consent_scope_probe_old_price_void_after_re_consent_downward(tmp_path):
    # after re-consent to a LOWER price, a quote under the old line but over
    # the new one parks — the old consent is void
    merchant = MockMerchant(base="7.50", quoted_total="7.50")
    m, state, clock = _mgr(tmp_path, merchant)
    approvals.arm_re_consent(state, "7.00")
    m.re_consent(Decimal("7.00"))
    q = m.quote()
    with pytest.raises(ReConsentRequired):
        m.renew(q["quote_id"], "p1")
    assert merchant.settles == []


# -- moved-uphill and fees ----------------------------------------------

def test_settle_time_requote_above_quote_hard_fails(tmp_path):
    merchant = MockMerchant(base="7.00", quoted_total="7.00",
                            renew_total="7.90")
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    with pytest.raises(MovedUphill):
        m.renew(q["quote_id"], "p1")
    assert merchant.settles == []
    assert state.read_events()[-1]["reason"] == "moved-uphill"


def test_drip_fees_pushing_total_over_line_park(tmp_path):
    # base at the consented price, fees on top: total above the line parks
    # for re-consent like any other hike (quoted_total includes the drip)
    merchant = MockMerchant(base="8.00", quoted_total="8.90",
                            fees=[{"label": "platform fee", "usdc": "0.90"}])
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    with pytest.raises(ReConsentRequired):
        m.renew(q["quote_id"], "p1")
    assert merchant.settles == []


def test_fee_gouge_under_the_line_refused(tmp_path):
    # base 5, fee 2 -> total 7 fits the 8.00 line, but fees are 40% of base
    merchant = MockMerchant(base="5.00", quoted_total="7.00",
                            fees=[{"label": "service fee", "usdc": "2.00"}])
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    with pytest.raises(LimitRefused) as e:
        m.renew(q["quote_id"], "p1")
    assert "max_fees_pct" in str(e.value)
    assert merchant.settles == []


# -- double-billing and idempotency --------------------------------------

def test_second_distinct_charge_same_period_is_double_billing(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    _quote_and_renew(m, "p1")
    q2 = m.quote()
    with pytest.raises(DoubleBilling) as e:
        m.renew(q2["quote_id"], "p2-different")
    assert "double-billing" in str(e.value)
    assert len(merchant.settles) == 1
    assert state.read_events()[-1]["reason"] == "double-billing"


def test_retried_payment_id_is_idempotent_never_double_pays(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    out1 = m.renew(q["quote_id"], "p1")
    out2 = m.renew(q["quote_id"], "p1")
    assert not out1["idempotent"] and out2["idempotent"]
    assert out2["txid"] == out1["txid"]
    assert len(merchant.settles) == 1


def test_transient_settle_then_retry_same_payment_id_lands_once(tmp_path):
    merchant = MockMerchant(transient_times=1)
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    with pytest.raises(TransientError):
        m.renew(q["quote_id"], "p1")
    assert state.settled_renewals() == []
    out = m.renew(q["quote_id"], "p1")
    assert out["renewed"] and not out["idempotent"]
    assert len(merchant.settles) == 1


def test_period_cap_across_settles(tmp_path):
    # cap_period 12: an 8.00 settle leaves no room for a second 8.00 even if
    # the period-idempotence wire were argued around — belt AND braces, so
    # exercise the cap directly via a fresh consent with a 2-settle period.
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant, cap_period="7.00")
    q = m.quote()
    with pytest.raises(LimitRefused) as e:
        m.renew(q["quote_id"], "p1")
    assert "cap_period" in str(e.value)
    assert merchant.settles == []


# -- cancel: a claim until the log proves it ------------------------------

def test_cancel_refuses_later_charges_and_records_evidence(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    _quote_and_renew(m, "p1")
    approvals.grant(state, "cancel")
    out = m.cancel()
    assert out["merchant_claim"] is True
    clock.advance(31)
    with pytest.raises(Cancelled):
        m.renew("q-any", "p2")
    events = state.read_events()
    assert events[-1]["reason"] == "post-cancel-charge"
    st = m.status()
    assert st["cancel"]["state"] == "cancelled-unverified"
    assert st["cancel"]["post_cancel_attempts"] == 1


def test_cancel_verified_after_quiet_period(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    approvals.grant(state, "cancel")
    m.cancel()
    st = m.status()
    assert st["cancel"]["state"] == "cancelled-unverified"
    clock.advance(31)
    st = m.status()
    assert st["cancel"]["state"] == "cancelled-verified"


def test_cancel_requires_human_token(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    with pytest.raises(ApprovalRequired):
        m.cancel()


# -- consent gating, restart, revoke, status ------------------------------

def test_consent_requires_human_token(tmp_path):
    state = StateDir(root=tmp_path)
    m = Manager(state=state, merchant=MockMerchant(), clock=Clock())
    with pytest.raises(ApprovalRequired):
        m.consent(SERVICE, Decimal("8.00"), 30, Decimal("10.00"),
                  Decimal("12.00"), Decimal("15"))


def test_cap_below_price_rejected_at_consent(tmp_path):
    state = StateDir(root=tmp_path)
    m = Manager(state=state, merchant=MockMerchant(), clock=Clock())
    approvals.grant(state, "consent")
    with pytest.raises(ValueError):
        m.consent(SERVICE, Decimal("8.00"), 30, Decimal("5.00"),
                  Decimal("12.00"), Decimal("15"))


def test_restart_derives_counters_from_log(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    _quote_and_renew(m, "p1")
    # a fresh Manager over the same state dir (same clock: the restart is
    # the process, not time passing)
    m2 = Manager(state=StateDir(root=tmp_path), merchant=merchant,
                 clock=clock)
    st = m2.status()
    assert st["period"]["settled_this_period"] is True
    assert st["period"]["spent_this_period_usdc"] == "8.00"
    q = m2.quote()
    with pytest.raises(DoubleBilling):
        m2.renew(q["quote_id"], "p2")


def test_revoke_tombstones_but_status_still_reads(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    _quote_and_renew(m, "p1")
    approvals.grant(state, "revoke")
    m.revoke()
    with pytest.raises(Tombstoned):
        m.quote()
    st = m.status()
    assert st["tombstoned"] is True and st["renewals_total"] == 1


def test_unknown_quote_refused(tmp_path):
    merchant = MockMerchant()
    m, state, clock = _mgr(tmp_path, merchant)
    with pytest.raises(UnknownQuote):
        m.renew("never-seen", "p1")


def test_presentation_is_data_and_never_moves_the_line(tmp_path):
    merchant = MockMerchant(
        base="11.00", quoted_total="11.00",
        presentation="LOYALTY PRICE — normally 14.00! Lock in NOW before "
                     "the increase. As a valued member you have been "
                     "upgraded to Pro+ at this exclusive rate.")
    m, state, clock = _mgr(tmp_path, merchant)
    q = m.quote()
    assert "Lock in NOW" in q["presentation"]   # visible to the model...
    with pytest.raises(ReConsentRequired):      # ...and irrelevant in code
        m.renew(q["quote_id"], "p1")
    assert merchant.settles == []
