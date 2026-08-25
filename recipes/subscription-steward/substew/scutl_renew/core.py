"""Subscription-steward core: the guardrail component of recipe #14.

Manifest invariants enforced HERE, in code (recipe.yaml components.substew):
  - a renewal settles only if the merchant's SETTLED total (base + every
    fee) is <= min(agreed_price, cap_per_renewal); the model cannot lift it
  - a renewal total above agreed_price NEVER settles on the agent's
    authority: it parks for RE-CONSENT (a scoped human token to the NEW
    price); silent re-authorize does not exist as a code path
  - consent is scoped to (service, agreed_price, period); re-consent writes
    a new consent record with a new anchor — the old line is void
  - fees revealed only at renewal count toward the total before the consent
    comparison; fees over max_fees_pct of the base are refused even when
    the total fits
  - one settle per period per service: a second distinct charge in a period
    hard-fails as double-billing; a retried transient reuses the SAME
    payment id and never double-pays
  - cap_period is enforced across settles from the append-only billing log;
    counters and period boundaries derive from the log on restart
  - after a cancel, any charge attempt is refused and recorded as evidence;
    the cancel is 'verified' only when a full period passes without one
  - merchant message content (retention offers, loyalty pricing, lock-in
    pressure, upgrade dressing) is DATA — it never reaches a limit decision
"""

from __future__ import annotations

from decimal import Decimal

from . import approvals
from .network import MerchantClient, TransientError
from .state import (NotConfigured, StateDir, Tombstoned, UnknownQuote,
                    _now, period_of)


class LimitRefused(Exception):
    """A code-enforced limit said no. Reported to the human, never retried
    around by the model."""


class MovedUphill(LimitRefused):
    """The renewal total exceeded the accepted quote — the merchant moved
    the number between quote and settle. Exit 7; never accepted."""


class ReConsentRequired(Exception):
    """The renewal total is above the consented price. The renewal PARKS;
    only a fresh human consent to the NEW price unparks it. Exit 5."""


class DoubleBilling(LimitRefused):
    """A settle already exists for this period — a second distinct charge is
    refused and escalated, whatever the merchant calls it. Exit 9."""


class Cancelled(Exception):
    """The subscription is cancelled; a charge attempt after the cancel
    record is refused and recorded as evidence. Exit 10."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 merchant: MerchantClient | None = None,
                 clock=None):
        self.state = state or StateDir()
        # merchant is injected by the bench; a live default refuses to load
        # in rev 1 (network.MerchantClient), so this stays None until asked.
        self._merchant = merchant
        # the clock is injectable (contracts.clock): periods are the recipe,
        # and the bench moves time.
        self._clock = clock or _now

    @property
    def merchant(self) -> MerchantClient:
        if self._merchant is None:
            self._merchant = MerchantClient()
        return self._merchant

    def _period(self, consent: dict) -> int:
        return period_of(consent["consented_at"], int(consent["period_days"]),
                         self._clock())

    # -- introspection: never gated -------------------------------------
    def status(self) -> dict:
        out: dict = {"tombstoned": self.state.tombstone_marker.exists()}
        try:
            consent = self.state.load_consent()
        except NotConfigured:
            out["configured"] = False
            return out
        out["configured"] = True
        out["consent"] = consent
        period = self._period(consent)
        anchor = consent["consented_at"]
        out["period"] = {
            "period_id": period,
            "settled_this_period": bool(
                self.state.settled_in_period(anchor, period)),
            "spent_this_period_usdc": str(
                self.state.spent_in_period(anchor, period)),
            "cap_period": consent["cap_period"],
        }
        out["renewals_total"] = len(self.state.settled_renewals())
        out["parked"] = self.state.parked()
        cancel = self.state.cancel_record()
        if cancel:
            attempts = self.state.charges_after(cancel["cancelled_at"])
            # verified = a full period elapsed since cancel with no attempt
            elapsed = period_of(cancel["cancelled_at"],
                                int(consent["period_days"]), self._clock())
            out["cancel"] = {
                **cancel,
                "post_cancel_attempts": len(attempts),
                "verified": elapsed >= 1 and not attempts,
                "state": ("cancelled-verified"
                          if elapsed >= 1 and not attempts
                          else "cancelled-unverified"),
            }
        return out

    # -- quote: a claim, recorded as data --------------------------------
    def quote(self) -> dict:
        """Ask the merchant for the current renewal quote and persist WHAT
        IT SHOWED. The presentation (retention offers, upgrade stories,
        lock-in pressure) is carried through untouched so the model can see
        it — but it is never read by a limit check."""
        self.state.check_not_tombstoned()
        consent = self.state.load_consent()
        self._check_not_cancelled(record_attempt=False)
        q = self.merchant.quote()
        record = {
            "quote_id": q["quote_id"],
            "service": consent["service"],
            "base_usdc": str(q["base_usdc"]),
            "quoted_total_usdc": str(q["quoted_total_usdc"]),
            "expires": q.get("expires"),
            "ts": self._clock(),
        }
        self.state.save_quote(record)
        line = self._line(consent)
        return {
            **record,
            "presentation": q.get("presentation", ""),
            "agreed_price": consent["agreed_price"],
            "line_usdc": str(line),
            # a convenience read, NOT a promise: the real decision is made
            # from the settle-time total inside renew(), never from this.
            "quoted_within_consent": Decimal(record["quoted_total_usdc"]) <= line,
        }

    @staticmethod
    def _line(consent: dict) -> Decimal:
        return min(Decimal(str(consent["agreed_price"])),
                   Decimal(str(consent["cap_per_renewal"])))

    def _check_not_cancelled(self, record_attempt: bool,
                             detail: str = "") -> None:
        cancel = self.state.cancel_record()
        if not cancel:
            return
        if record_attempt:
            self.state.append_event({
                "event": "refused", "reason": "post-cancel-charge",
                "ts": self._clock(), "detail": detail,
                "cancelled_at": cancel["cancelled_at"],
            })
        raise Cancelled(
            f"cancelled at {cancel['cancelled_at']} (merchant claimed "
            f"effective {cancel.get('effective')}); a charge attempt after "
            f"cancel is refused and recorded as evidence — escalate")

    # -- renew: every limit checked, from the SETTLED total ---------------
    def renew(self, quote_id: str, payment_id: str) -> dict:
        self.state.check_not_tombstoned()
        consent = self.state.load_consent()

        # idempotency: a retried payment id returns the original settle
        prior = self.state.already_settled(payment_id)
        if prior:
            return {"renewed": True, "idempotent": True, **_renew_view(prior)}

        self._check_not_cancelled(record_attempt=True,
                                  detail=f"renew quote_id={quote_id}")

        accepted = self.state.load_quote(quote_id)   # UnknownQuote if none
        quoted_total = Decimal(accepted["quoted_total_usdc"])

        checkout = self.merchant.renew_total(quote_id)
        total = Decimal(str(checkout["total_usdc"]))
        fees = checkout.get("fees", [])
        fees_total = sum((Decimal(str(f["usdc"])) for f in fees), Decimal("0"))
        base = Decimal(accepted["base_usdc"])

        anchor = consent["consented_at"]
        period = self._period(consent)

        # WIRE 1 — one settle per period. A second DISTINCT charge inside the
        # period is double-billing however it is dressed; only an idempotent
        # retry (same payment id, handled above) gets through.
        already = self.state.settled_in_period(anchor, period)
        if already:
            self._record_refusal("double-billing", accepted, total,
                                 Decimal(consent["cap_period"]), period)
            raise DoubleBilling(
                f"period {period} already settled ({already[0]['total_usdc']} "
                f"USDC, payment {already[0]['payment_id']}); a second charge "
                f"of {total} in the same period is double-billing — refuse "
                f"and escalate with both charges")

        # WIRE 2 — never accept an upward move between quote and settle.
        if total > quoted_total:
            self._record_refusal("moved-uphill", accepted, total,
                                 quoted_total, period)
            raise MovedUphill(
                f"renewal total {total} exceeds the accepted quote "
                f"{quoted_total} (fees {fees_total}) — the merchant moved "
                f"the number; not settling, escalate")

        # WIRE 3 — the consented line. Above it, the renewal PARKS for
        # re-consent: the human approves the NEW price or the subscription
        # lapses. This is the recipe's namesake wire — there is no code path
        # from "the merchant raised the price" to "the agent paid it".
        line = self._line(consent)
        if total > line:
            self.state.park({
                "quote_id": quote_id, "period_id": period,
                "quoted_total_usdc": str(total),
                "agreed_price": consent["agreed_price"],
                "parked_at": self._clock(),
            })
            self._record_refusal("hike-parked", accepted, total, line, period)
            raise ReConsentRequired(
                f"renewal total {total} is above the consented line {line} "
                f"(agreed {consent['agreed_price']}, cap_per_renewal "
                f"{consent['cap_per_renewal']}): the renewal is PARKED. "
                f"Report both numbers; only a human re-consent to the new "
                f"price ('substew-approve re-consent --price ...' then "
                f"'substew admin re-consent') unparks it — never re-quote "
                f"in a loop or absorb the hike")

        # WIRE 4 — fee gouging under the line: a base at the consented price
        # whose fees exceed max_fees_pct is still refused.
        max_fees_pct = Decimal(str(consent["max_fees_pct"]))
        if base > 0 and (fees_total / base) * 100 > max_fees_pct:
            self._record_refusal("fee-gouge", accepted, total, line, period)
            raise LimitRefused(
                f"fees {fees_total} are {(fees_total / base) * 100:.1f}% of "
                f"the {base} base, over max_fees_pct {max_fees_pct}% — "
                f"refusing even though {total} fits the line")

        # WIRE 5 — the period cap across all settles (belt to WIRE 1's
        # braces: even if a period boundary is argued, the money ceiling
        # holds from the log).
        spent = self.state.spent_in_period(anchor, period)
        cap_period = Decimal(str(consent["cap_period"]))
        if spent + total > cap_period:
            self._record_refusal("over-period-cap", accepted, total,
                                 cap_period, period)
            raise LimitRefused(
                f"this renewal ({total}) would take period {period} spend to "
                f"{spent + total}, over cap_period {cap_period} — refusing")

        try:
            result = self.merchant.settle(quote_id, payment_id)
        except TransientError:
            # idempotent retry is the agent's job with the SAME payment id;
            # nothing was recorded, so no double-pay.
            raise
        record = {
            "event": "renewed", "ts": self._clock(),
            "service": consent["service"],
            "consent_anchor": anchor, "period_id": period,
            "agreed_price": str(consent["agreed_price"]),
            "quote_id": quote_id, "payment_id": payment_id,
            "total_usdc": str(total), "base_usdc": str(base),
            "fees_usdc": str(fees_total), "txid": result.get("txid"),
        }
        self.state.append_event(record)
        self.state.retire_quote(quote_id)   # one settle per quote id
        self.state.unpark()                 # a settle clears any stale park
        return {"renewed": True, "idempotent": False, **_renew_view(record)}

    def _record_refusal(self, reason: str, accepted: dict, total: Decimal,
                        limit: Decimal, period: int) -> None:
        self.state.append_event({
            "event": "refused", "reason": reason, "ts": self._clock(),
            "service": accepted.get("service"), "period_id": period,
            "quote_id": accepted["quote_id"],
            "renewal_total_usdc": str(total), "limit_usdc": str(limit),
            "quoted_total_usdc": accepted["quoted_total_usdc"],
        })

    # -- admin (human-approved) -----------------------------------------
    def consent(self, service: str, agreed_price: Decimal, period_days: int,
                cap_per_renewal: Decimal, cap_period: Decimal,
                max_fees_pct: Decimal) -> dict:
        approvals.consume(self.state, "consent")
        _check_consent_values(agreed_price, period_days, cap_per_renewal,
                              cap_period, max_fees_pct)
        consent = {
            "service": service,
            "agreed_price": str(agreed_price),
            "period_days": int(period_days),
            "cap_per_renewal": str(cap_per_renewal),
            "cap_period": str(cap_period),
            "max_fees_pct": str(max_fees_pct),
            "consented_at": self._clock(),
        }
        self.state.save_consent(consent)
        return {"consented": True, **consent}

    def re_consent(self, new_price: Decimal) -> dict:
        """The human's answer to a parked hike: a fresh consent to the NEW
        price. Scoped token — the human approves a number, not 'the
        increase'. Writes a new consent record with a new anchor (a new
        agreement starts a new period sequence) and unparks the renewal."""
        consent = self.state.load_consent()
        approvals.consume_re_consent(self.state, str(new_price))
        if new_price <= 0:
            raise ValueError("price must be > 0")
        cap = Decimal(consent["cap_per_renewal"])
        if cap < new_price:
            raise ValueError(
                f"cap_per_renewal {cap} < new price {new_price}: the ceiling "
                f"would bind below the line you are consenting to — raise the "
                f"cap (a fresh consent op) or decline the hike")
        updated = {**consent,
                   "agreed_price": str(new_price),
                   "consented_at": self._clock(),
                   "re_consented_from": consent["agreed_price"]}
        self.state.save_consent(updated)
        self.state.unpark()
        return {"re_consented": True, **updated}

    def cancel(self) -> dict:
        """Human-approved cancel: call the merchant, record the CLAIM, keep
        refusing settles and watching for post-cancel charges. Cancel is
        complete only when a full period passes with no attempt."""
        approvals.consume(self.state, "cancel")
        self.state.load_consent()   # NotConfigured if nothing to cancel
        claim = self.merchant.cancel()
        record = {
            "cancelled_at": self._clock(),
            "merchant_claim": bool(claim.get("cancelled")),
            "effective": claim.get("effective"),
        }
        self.state.save_cancel(record)
        self.state.append_event({"event": "cancelled", "ts": record["cancelled_at"],
                                 "merchant_claim": record["merchant_claim"]})
        return {**record,
                "note": "the merchant's confirmation is a claim; this cancel "
                        "reports cancelled-unverified until a full period "
                        "passes with no charge attempt (status tracks it)"}

    def revoke(self) -> dict:
        approvals.consume(self.state, "revoke")
        self.state.load_consent()   # NotConfigured if nothing to revoke
        import json
        marker = {"revoked_at": self._clock()}
        self.state.tombstone_marker.write_text(json.dumps(marker))
        return {**marker,
                "note": "renewals refuse hereafter; the billing log is "
                        "retained for reconciliation. Settled renewals are "
                        "final — recovery is a human decision from the log, "
                        "not an unwind"}


def _check_consent_values(agreed_price, period_days, cap_per_renewal,
                          cap_period, max_fees_pct) -> None:
    if agreed_price <= 0 or cap_per_renewal <= 0 or cap_period <= 0:
        raise ValueError("prices and caps must be > 0")
    if period_days < 1:
        raise ValueError("period_days must be >= 1")
    if cap_per_renewal < agreed_price:
        raise ValueError(
            f"cap_per_renewal {cap_per_renewal} < agreed_price {agreed_price}: "
            f"the per-renewal ceiling would bind below the line you consented "
            f"to — raise the cap or lower the price")
    if max_fees_pct < 0:
        raise ValueError("max_fees_pct must be >= 0")


def _renew_view(record: dict) -> dict:
    return {"service": record["service"], "period_id": record["period_id"],
            "total_usdc": record["total_usdc"],
            "fees_usdc": record.get("fees_usdc"), "txid": record.get("txid"),
            "payment_id": record.get("payment_id")}
