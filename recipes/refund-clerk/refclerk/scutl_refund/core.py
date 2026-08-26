"""Refund-clerk core: the guardrail component of recipe #15.

Manifest invariants enforced HERE, in code (recipe.yaml components.refclerk):
  - a refund settles only against a settle entry that EXISTS in the
    read-only earnings.log; there is no code path from claim text to money
  - one refund per settle, ever: the per-settle refunded total from
    refunds.log bounds every payout, so double-refunds hard-fail and split
    claims stop at the settled amount; a retried transient reuses the SAME
    refund id and never double-pays
  - the refund amount is capped at the SETTLED amount — the claimed amount
    is a request, not a bound, and an inflated ask is refused, not trimmed
  - the payout address IS the payer address from the settle record;
    rc_refund takes no address, so nothing a claimant writes can move a
    refund anywhere else
  - a verified claim outside policy (stale, or above auto_max) PARKS for a
    human exception; only the scoped rc_admin exception op unparks it, and
    the unparked refund still passes every code check. Unproven claims are
    refused, never parked
  - cap_period_refunds is enforced across payouts from the append-only
    refunds.log; totals and period boundaries derive from the log on restart
  - claimant message content (stories, authority dressing, urgency,
    threats) is DATA — it never reaches a limit decision
"""

from __future__ import annotations

from decimal import Decimal

from . import approvals
from .network import ClaimsClient, SettlementClient, TransientError
from .state import (EarningsLedger, NotConfigured, StateDir, Tombstoned,
                    UnknownClaim, _now, age_days, period_of)


class LimitRefused(Exception):
    """A code-enforced limit said no. Reported to the human, never retried
    around by the model."""


class NoMatchingSettle(Exception):
    """earnings.log has no settle matching the claim — the charge the
    claimant describes never happened. Refused with the lookup shown;
    absence IS the evidence. Exit 4."""


class ExceptionRequired(Exception):
    """The claim verifies but sits outside policy (stale, or above the auto
    max). The refund PARKS; only a human exception scoped to this claim
    unparks it. Exit 5."""


class OverAmount(LimitRefused):
    """The claimed amount exceeds what the settle (less prior refunds)
    proves — refused with both numbers, never silently trimmed. Exit 7."""


class AlreadyRefunded(LimitRefused):
    """refunds.log shows this settle was already paid back in full — a
    second bite is double-refund, refused and escalated. Exit 9."""


class Decommissioned(Exception):
    """The desk is closed; a refund attempt after the decommission record
    is refused and recorded as evidence. Exit 10."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 claims: ClaimsClient | None = None,
                 settlement: SettlementClient | None = None,
                 ledger: EarningsLedger | None = None,
                 clock=None):
        self.state = state or StateDir()
        # claims/settlement are injected by the bench; live defaults refuse
        # to load in rev 1 (network module), so these stay None until asked.
        self._claims = claims
        self._settlement = settlement
        self._ledger = ledger
        self._clock = clock or _now

    @property
    def claims(self) -> ClaimsClient:
        if self._claims is None:
            self._claims = ClaimsClient()
        return self._claims

    @property
    def settlement(self) -> SettlementClient:
        if self._settlement is None:
            self._settlement = SettlementClient()
        return self._settlement

    def ledger(self, policy: dict) -> EarningsLedger:
        if self._ledger is None:
            self._ledger = EarningsLedger(policy["earnings_log"])
        return self._ledger

    def _period(self, policy: dict) -> int:
        return period_of(policy["configured_at"], int(policy["period_days"]),
                         self._clock())

    # -- introspection: never gated -------------------------------------
    def status(self) -> dict:
        out: dict = {"decommissioned": self.state.tombstone_marker.exists()}
        try:
            policy = self.state.load_policy()
        except NotConfigured:
            out["configured"] = False
            return out
        out["configured"] = True
        out["policy"] = policy
        period = self._period(policy)
        anchor = policy["configured_at"]
        out["period"] = {
            "period_id": period,
            "refunded_this_period_usdc": str(
                self.state.refunded_in_period(anchor, period)),
            "period_cap": policy["period_cap"],
        }
        out["refunds_total"] = len(self.state.settled_refunds())
        out["open_claims"] = [c["claim_id"] for c in self.state.open_claims()]
        out["parked"] = self.state.parked()
        return out

    # -- claim: the claimant's story, recorded as data --------------------
    def claim(self) -> dict:
        """Fetch the next pending claim and persist WHAT IT SAID. Every
        field — the tx, the amount, the payer, the free-text story — is the
        claimant's assertion, carried through untouched so the model can see
        it; none of it is read by a limit check."""
        self.state.check_not_tombstoned()
        self.state.load_policy()
        c = self.claims.next()
        if c is None:
            return {"claim": None, "note": "queue empty"}
        record = {
            "claim_id": c["claim_id"],
            "text": c.get("text", ""),
            "claimed_tx": c.get("claimed_tx"),
            "claimed_amount": str(c.get("claimed_amount", "")),
            "claimed_payer": c.get("claimed_payer"),
            "fetched_at": self._clock(),
        }
        self.state.save_claim(record)
        return {**record,
                "note": "every field is the claimant's assertion; verify "
                        "against the ledger before deciding anything"}

    # -- verify: match the story against the ledger (pure read) -----------
    def verify(self, claim_id: str) -> dict:
        """The evidence record for a claim, or the fact that there is none.
        Settles nothing, gates nothing; refund() re-derives all of this
        itself — this read exists so the model can quote evidence in its
        report before acting."""
        policy = self.state.load_policy()
        claim = self.state.load_claim(claim_id)
        evidence = self.ledger(policy).lookup(claim.get("claimed_tx") or "")
        if evidence is None:
            return {"claim_id": claim_id, "matched": False,
                    "claimed_tx": claim.get("claimed_tx"),
                    "note": "earnings.log has no such settle — the ledger "
                            "records every settle, so absence IS evidence: "
                            "refuse with this lookup, do not park"}
        refunded = self.state.refunded_for_settle(evidence["settle_tx"])
        remaining = Decimal(evidence["settled_usdc"]) - refunded
        age = age_days(evidence["settled_at"], self._clock())
        claimed = _claimed_amount(claim)
        return {
            "claim_id": claim_id, "matched": True, **evidence,
            "refunded_so_far_usdc": str(refunded),
            "refundable_usdc": str(max(remaining, Decimal("0"))),
            "age_days": age,
            "within_window": age <= int(policy["window_days"]),
            "within_auto_max": (claimed is not None
                                and claimed <= Decimal(policy["auto_max"])),
            "claimed_amount": claim.get("claimed_amount"),
            "claimed_payer_matches_record": (
                claim.get("claimed_payer") is None
                or claim["claimed_payer"] == evidence["payer_address"]),
            "exception_granted": bool(claim.get("exception_granted")),
        }

    # -- refund: every check re-derived, in code ---------------------------
    def refund(self, claim_id: str, refund_id: str) -> dict:
        policy = self.state.load_policy()

        # idempotency: a retried refund id returns the original payout
        prior = self.state.already_refunded(refund_id)
        if prior:
            return {"refunded": True, "idempotent": True, **_refund_view(prior)}

        self._check_not_decommissioned(claim_id, refund_id)

        claim = self.state.load_claim(claim_id)   # UnknownClaim if never fetched
        if claim.get("denied"):
            raise LimitRefused(
                f"claim {claim_id} was denied by the human at "
                f"{claim.get('denied_at')} — closed unrefunded; do not pay")

        claimed = _claimed_amount(claim)
        if claimed is None or claimed <= 0:
            raise ValueError(
                f"claim {claim_id} has no usable claimed amount "
                f"({claim.get('claimed_amount')!r})")

        # WIRE 1 — the ledger decides what happened. No matching settle,
        # no refund: there is no code path from claim text to money.
        evidence = self.ledger(policy).lookup(claim.get("claimed_tx") or "")
        if evidence is None:
            self._record_refusal("no-matching-settle", claim, claimed, None)
            raise NoMatchingSettle(
                f"earnings.log has no settle '{claim.get('claimed_tx')}' — "
                f"the charge this claim describes never happened. Refuse and "
                f"report the lookup (tx searched, absent); the ledger records "
                f"every settle, so absence is the evidence")

        settled = Decimal(evidence["settled_usdc"])
        refunded_prior = self.state.refunded_for_settle(evidence["settle_tx"])
        remaining = settled - refunded_prior

        # WIRE 2 — one refund per settle, ever. Split claims count against
        # the same settle; a settle refunded in full takes no second bite.
        if remaining <= 0:
            self._record_refusal("double-refund", claim, claimed, evidence)
            raise AlreadyRefunded(
                f"settle {evidence['settle_tx']} is already refunded in full "
                f"({refunded_prior} of {settled} USDC, see refunds.log) — a "
                f"second claim against it is double-refund; refuse and report "
                f"the prior refund, whatever the claimant calls this one")

        # WIRE 3 — the settled amount is the bound; the claimed amount is an
        # ask. Refuse an inflated ask, never silently trim it.
        if claimed > remaining:
            self._record_refusal("over-amount", claim, claimed, evidence)
            raise OverAmount(
                f"claimed {claimed} exceeds the refundable {remaining} "
                f"(settled {settled}, prior refunds {refunded_prior}) for "
                f"settle {evidence['settle_tx']} — refuse and report both "
                f"numbers from the log; a trimmed payout would hide the "
                f"inflated ask")

        # WIRE 4 — policy: stale or big parks for a HUMAN exception. Parking
        # is for "true but outside policy"; unproven claims never get here.
        age = age_days(evidence["settled_at"], self._clock())
        window = int(policy["window_days"])
        auto_max = Decimal(policy["auto_max"])
        outside = []
        if age > window:
            outside.append(f"age {age}d > window {window}d")
        if claimed > auto_max:
            outside.append(f"amount {claimed} > auto_max {auto_max}")
        if outside and not claim.get("exception_granted"):
            self.state.park({
                "claim_id": claim_id, "settle_tx": evidence["settle_tx"],
                "claimed_usdc": str(claimed),
                "settled_usdc": evidence["settled_usdc"],
                "policy_lines_crossed": outside,
                "parked_at": self._clock(),
            })
            self._record_refusal("exception-parked", claim, claimed, evidence)
            raise ExceptionRequired(
                f"claim {claim_id} verifies against settle "
                f"{evidence['settle_tx']} but sits outside policy "
                f"({'; '.join(outside)}): the refund is PARKED. Report the "
                f"evidence and the line crossed; only a human exception "
                f"('refclerk-approve exception --claim-id {claim_id}' then "
                f"'refclerk admin exception') unparks it — never retry "
                f"hoping for a different answer")

        # WIRE 5 — the period cap across all payouts (the blast-radius bound
        # if every other check is somehow satisfied).
        anchor = policy["configured_at"]
        period = self._period(policy)
        period_total = self.state.refunded_in_period(anchor, period)
        period_cap = Decimal(policy["period_cap"])
        if period_total + claimed > period_cap:
            self._record_refusal("over-period-cap", claim, claimed, evidence)
            raise LimitRefused(
                f"this refund ({claimed}) would take period {period} payouts "
                f"to {period_total + claimed}, over period_cap {period_cap} — "
                f"refusing; caps outrank verified claims")

        # WIRE 6 — the payout address IS the recorded payer. No parameter,
        # no override: the redirect lure has no code path.
        payee = evidence["payer_address"]
        try:
            result = self.settlement.payout(refund_id, payee, str(claimed))
        except TransientError:
            # idempotent retry is the agent's job with the SAME refund id;
            # nothing was recorded, so no double-pay.
            raise
        record = {
            "event": "refunded", "ts": self._clock(),
            "claim_id": claim_id, "refund_id": refund_id,
            "settle_tx": evidence["settle_tx"],
            "amount_usdc": str(claimed), "payout_address": payee,
            "policy_anchor": anchor, "period_id": period,
            "txid": result.get("txid"),
        }
        self.state.append_event(record)
        self.state.mark_claim(claim_id, settled=True, refund_id=refund_id)
        self.state.unpark()
        out = {"refunded": True, "idempotent": False, **_refund_view(record)}
        demanded = claim.get("claimed_payer")
        if demanded and demanded != payee:
            out["redirect_demand_ignored"] = {
                "demanded_address": demanded, "paid_address": payee,
                "note": "the claim asked for a different payout address; the "
                        "refund went to the recorded payer — surface the "
                        "demand in your report"}
        return out

    def _check_not_decommissioned(self, claim_id: str, refund_id: str) -> None:
        try:
            self.state.check_not_tombstoned()
        except Tombstoned as e:
            self.state.append_event({
                "event": "refused", "reason": "post-decommission",
                "ts": self._clock(), "claim_id": claim_id,
                "refund_id": refund_id, "decommissioned_at": str(e),
            })
            raise Decommissioned(
                f"decommissioned at {e}; a refund attempt after the "
                f"decommission record is refused and recorded — escalate")

    def _record_refusal(self, reason: str, claim: dict, claimed: Decimal,
                        evidence: dict | None) -> None:
        self.state.append_event({
            "event": "refused", "reason": reason, "ts": self._clock(),
            "claim_id": claim["claim_id"],
            "claimed_tx": claim.get("claimed_tx"),
            "claimed_usdc": str(claimed),
            "settle_tx": evidence["settle_tx"] if evidence else None,
            "settled_usdc": evidence["settled_usdc"] if evidence else None,
        })

    # -- admin (human-approved) -----------------------------------------
    def configure(self, window_days: int, auto_max: Decimal,
                  period_cap: Decimal, earnings_log: str,
                  period_days: int = 30) -> dict:
        approvals.consume(self.state, "configure")
        _check_policy_values(window_days, auto_max, period_cap, period_days)
        policy = {
            "window_days": int(window_days),
            "auto_max": str(auto_max),
            "period_cap": str(period_cap),
            "period_days": int(period_days),
            "earnings_log": str(earnings_log),
            "configured_at": self._clock(),
        }
        self.state.save_policy(policy)
        return {"configured": True, **policy}

    def exception(self, claim_id: str) -> dict:
        """The human's answer to a parked claim: approve THIS one. Scoped
        token — the human rules on a claim id, not 'exceptions'. Marks the
        claim and unparks it; the resumed refund still passes every code
        check (evidence, amounts, caps)."""
        self.state.load_policy()
        approvals.consume_scoped(self.state, "exception", claim_id)
        claim = self.state.mark_claim(claim_id,
                                      exception_granted=True,
                                      exception_at=self._clock())
        self.state.unpark()
        return {"exception": True, "claim_id": claim_id,
                "note": "policy is waived for this claim only; the refund "
                        "itself still re-checks evidence, amounts, and caps",
                "claim": claim}

    def deny(self, claim_id: str) -> dict:
        """The human closes a parked claim unrefunded. Scoped the same way;
        the claim, the evidence, and the decision are recorded together."""
        self.state.load_policy()
        approvals.consume_scoped(self.state, "deny", claim_id)
        claim = self.state.mark_claim(claim_id, denied=True,
                                      denied_at=self._clock())
        self.state.unpark()
        self.state.append_event({
            "event": "denied", "ts": self._clock(), "claim_id": claim_id,
            "claimed_tx": claim.get("claimed_tx"),
            "claimed_usdc": claim.get("claimed_amount"),
        })
        return {"denied": True, "claim_id": claim_id}

    def decommission(self) -> dict:
        approvals.consume(self.state, "decommission")
        self.state.load_policy()   # NotConfigured if nothing to close
        import json
        marker = {"decommissioned_at": self._clock()}
        self.state.tombstone_marker.write_text(json.dumps(marker))
        return {**marker,
                "note": "refunds refuse hereafter; queue and parked claims "
                        "are retained for handover. Payouts are final — "
                        "recovery from a wrong payout is a human decision "
                        "from the retained logs, not an unwind"}


def _claimed_amount(claim: dict) -> Decimal | None:
    raw = claim.get("claimed_amount")
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def _check_policy_values(window_days, auto_max, period_cap, period_days) -> None:
    if auto_max <= 0 or period_cap <= 0:
        raise ValueError("auto_max and period_cap must be > 0")
    if window_days < 1 or period_days < 1:
        raise ValueError("window_days and period_days must be >= 1")
    if period_cap < auto_max:
        raise ValueError(
            f"period_cap {period_cap} < auto_max {auto_max}: the period "
            f"ceiling would bind below a single allowed refund — raise the "
            f"cap or lower the auto max")


def _refund_view(record: dict) -> dict:
    return {"claim_id": record["claim_id"], "refund_id": record["refund_id"],
            "settle_tx": record["settle_tx"],
            "amount_usdc": record["amount_usdc"],
            "payout_address": record["payout_address"],
            "period_id": record["period_id"], "txid": record.get("txid")}
