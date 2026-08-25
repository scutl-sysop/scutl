"""Price-watch core: the guardrail component of recipe #25.

Manifest invariants enforced HERE, in code (recipe.yaml components.pricewatch):
  - a buy settles only if the merchant's SETTLED total (sticker + every
    fee) is <= min(target_price, cap_per_buy); the model cannot lift it
  - a buy-time total ABOVE the accepted quote hard-fails (MovedUphill) —
    the tool never silently accepts an upward re-quote, even under the cap
  - fees revealed only at checkout count toward the total before the cap
    decision; and a checkout whose fees exceed max_fees_pct of the sticker
    is refused even when the total still fits
  - cap_daily is enforced across buys from the append-only spend log
  - the FIRST buy for a (item, target) needs a scoped human token; a target
    change re-arms the gate; later buys under the same target are unattended
  - merchant message content (personalized prices, anchors, scarcity) is
    DATA — it never reaches a limit decision, so it cannot move the line
  - one settle per quote id; a retried buy reuses the payment id and never
    double-pays; status (reading the record) is never gated
"""

from __future__ import annotations

from decimal import Decimal

from . import approvals
from .network import MerchantClient, TransientError
from .state import (NotConfigured, StateDir, Tombstoned, UnknownQuote,
                    _now)


class LimitRefused(Exception):
    """A code-enforced limit said no. Reported to the human, never retried
    around by the model."""


class MovedUphill(LimitRefused):
    """The checkout total exceeded the accepted quote — the merchant moved
    the number between quote and buy. Exit 7; never accepted."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 merchant: MerchantClient | None = None):
        self.state = state or StateDir()
        # merchant is injected by the bench; a live default refuses to load
        # in rev 1 (network.MerchantClient), so this stays None until asked.
        self._merchant = merchant

    @property
    def merchant(self) -> MerchantClient:
        if self._merchant is None:
            self._merchant = MerchantClient()
        return self._merchant

    # -- introspection: never gated -------------------------------------
    def status(self) -> dict:
        out: dict = {"tombstoned": self.state.tombstone_marker.exists()}
        try:
            target = self.state.load_target()
        except NotConfigured:
            out["configured"] = False
            return out
        out["configured"] = True
        out["target"] = target
        buys = self.state.settled_buys()
        day_ago = _rolling_day_start()
        out["spend"] = {
            "buys_total": len(buys),
            "spent_today_usdc": str(self.state.spent_since(day_ago)),
            "cap_daily": target["cap_daily"],
        }
        out["first_buy_armed"] = self._first_buy_armed(
            target["item"], target["target_price"])
        return out

    def _first_buy_armed(self, item: str, target_price: str) -> bool:
        token = self.state.approvals / "first-buy.token"
        if not token.exists():
            return False
        import json
        scope = json.loads(token.read_text())
        return (scope.get("item") == item
                and scope.get("target_price") == str(target_price))

    # -- quote: a claim, recorded as data --------------------------------
    def quote(self, item: str) -> dict:
        """Ask the merchant and persist WHAT IT SHOWED. The presentation
        (anchors, scarcity, 'price for you') is carried through untouched so
        the model can see it — but it is never read by a limit check."""
        self.state.check_not_tombstoned()
        target = self.state.load_target()
        if item != target["item"]:
            raise LimitRefused(
                f"not watching '{item}'; the target is set for "
                f"'{target['item']}' (set-target is a human-approved op)")
        q = self.merchant.quote(item)
        record = {
            "quote_id": q["quote_id"],
            "item": item,
            "sticker_usdc": str(q["sticker_usdc"]),
            "quoted_total_usdc": str(q["quoted_total_usdc"]),
            "expires": q.get("expires"),
            "ts": _now(),
        }
        self.state.save_quote(record)
        ceiling = self._ceiling(target)
        return {
            **record,
            "presentation": q.get("presentation", ""),
            "target_price": target["target_price"],
            "ceiling_usdc": str(ceiling),
            # a convenience read, NOT a promise: the real decision is made
            # from the checkout total inside buy(), never from this field.
            "quoted_within_target": Decimal(record["quoted_total_usdc"]) <= ceiling,
        }

    @staticmethod
    def _ceiling(target: dict) -> Decimal:
        return min(Decimal(str(target["target_price"])),
                   Decimal(str(target["cap_per_buy"])))

    # -- buy: every limit checked, from the SETTLED total ----------------
    def buy(self, quote_id: str, payment_id: str) -> dict:
        self.state.check_not_tombstoned()
        target = self.state.load_target()

        # idempotency: a retried payment id returns the original settle
        prior = self.state.already_settled(payment_id)
        if prior:
            return {"bought": True, "idempotent": True, **_buy_view(prior)}

        accepted = self.state.load_quote(quote_id)   # UnknownQuote if none
        item = accepted["item"]
        quoted_total = Decimal(accepted["quoted_total_usdc"])

        checkout = self.merchant.checkout(quote_id)
        total = Decimal(str(checkout["total_usdc"]))
        fees = checkout.get("fees", [])
        fees_total = sum((Decimal(str(f["usdc"])) for f in fees), Decimal("0"))
        sticker = Decimal(accepted["sticker_usdc"])

        # WIRE 1 — never accept an upward move between quote and buy. Whether
        # the merchant re-quoted the base price or dripped fees, the total
        # rose above what we accepted; refuse and record the move.
        if total > quoted_total:
            self._record_refusal("moved-uphill", accepted, total, quoted_total)
            raise MovedUphill(
                f"checkout total {total} exceeds the accepted quote "
                f"{quoted_total} for '{item}' (fees {fees_total}) — the "
                f"merchant moved the number; not buying, escalate")

        # WIRE 2 — the fully-loaded total must hold under target and per-buy
        # cap. Fees revealed only now are already inside `total`.
        ceiling = self._ceiling(target)
        if total > ceiling:
            self._record_refusal("over-target", accepted, total, ceiling)
            raise LimitRefused(
                f"checkout total {total} for '{item}' is over the ceiling "
                f"{ceiling} (min of target {target['target_price']} and "
                f"cap_per_buy {target['cap_per_buy']}) — hold the line")

        # WIRE 3 — fee gouging under the ceiling: a sticker within target
        # whose fees exceed max_fees_pct is still refused.
        max_fees_pct = Decimal(str(target["max_fees_pct"]))
        if sticker > 0 and (fees_total / sticker) * 100 > max_fees_pct:
            self._record_refusal("fee-gouge", accepted, total, ceiling)
            raise LimitRefused(
                f"fees {fees_total} are {(fees_total / sticker) * 100:.1f}% of "
                f"the {sticker} sticker for '{item}', over max_fees_pct "
                f"{max_fees_pct}% — refusing even though {total} fits the cap")

        # WIRE 4 — rolling daily cap across all buys.
        spent_today = self.state.spent_since(_rolling_day_start())
        cap_daily = Decimal(str(target["cap_daily"]))
        if spent_today + total > cap_daily:
            self._record_refusal("over-daily-cap", accepted, total, cap_daily)
            raise LimitRefused(
                f"this buy ({total}) would take today's spend to "
                f"{spent_today + total}, over cap_daily {cap_daily} — refusing")

        # WIRE 5 — first-buy approval gate, scoped to (item, target_price).
        # Gates only the FIRST settle of this target; later buys under the
        # unchanged target are unattended. CHECK (not consume) before the
        # rail so an unapproved first buy never reaches settle; the token is
        # consumed only after a successful settle, so a transient failure
        # leaves the gate armed for the idempotent retry.
        is_first = not self.state.has_bought_for(item, target["target_price"])
        if is_first:
            approvals.check_first_buy(self.state, item, target["target_price"])

        try:
            result = self.merchant.settle(quote_id, payment_id)
        except TransientError:
            # idempotent retry is the agent's job with the SAME payment id;
            # nothing was recorded and the gate is still armed, so no
            # double-pay and no stranded approval.
            raise
        if is_first:
            approvals.consume_first_buy(self.state)
        record = {
            "event": "bought", "ts": _now(),
            "item": item, "target_price": str(target["target_price"]),
            "quote_id": quote_id, "payment_id": payment_id,
            "total_usdc": str(total), "sticker_usdc": str(sticker),
            "fees_usdc": str(fees_total), "txid": result.get("txid"),
        }
        self.state.append_event(record)
        self.state.retire_quote(quote_id)   # one settle per quote id
        return {"bought": True, "idempotent": False, **_buy_view(record)}

    def _record_refusal(self, reason: str, accepted: dict,
                        total: Decimal, limit: Decimal) -> None:
        self.state.append_event({
            "event": "refused", "reason": reason, "ts": _now(),
            "item": accepted["item"], "quote_id": accepted["quote_id"],
            "checkout_total_usdc": str(total), "limit_usdc": str(limit),
            "quoted_total_usdc": accepted["quoted_total_usdc"],
        })

    # -- admin (human-approved) -----------------------------------------
    def set_target(self, item: str, target_price: Decimal,
                   cap_per_buy: Decimal, cap_daily: Decimal,
                   max_fees_pct: Decimal) -> dict:
        approvals.consume(self.state, "set-target")
        if target_price <= 0 or cap_per_buy <= 0 or cap_daily <= 0:
            raise ValueError("prices and caps must be > 0")
        if cap_per_buy < target_price:
            raise ValueError(
                f"cap_per_buy {cap_per_buy} < target_price {target_price}: the "
                f"per-buy ceiling would bind below the line you're trying to "
                f"hold — raise the cap or lower the target")
        if max_fees_pct < 0:
            raise ValueError("max_fees_pct must be >= 0")
        target = {
            "item": item,
            "target_price": str(target_price),
            "cap_per_buy": str(cap_per_buy),
            "cap_daily": str(cap_daily),
            "max_fees_pct": str(max_fees_pct),
            "set_at": _now(),
        }
        self.state.save_target(target)
        return {"target_set": True, **target}

    def approve_first_buy(self, item: str) -> dict:
        """The human's arming act. Scoped to the CURRENT target price, so it
        cannot silently pre-approve a future, higher line."""
        target = self.state.load_target()
        if item != target["item"]:
            raise ValueError(
                f"target is set for '{target['item']}', not '{item}'")
        path = approvals.arm_first_buy(self.state, item, target["target_price"])
        return {"approved_for": {"item": item,
                                 "target_price": target["target_price"]},
                "token": path}

    def revoke(self) -> dict:
        approvals.consume(self.state, "revoke")
        self.state.load_target()   # NotConfigured if nothing to revoke
        import json
        marker = {"revoked_at": _now()}
        self.state.tombstone_marker.write_text(json.dumps(marker))
        return {**marker,
                "note": "buys refuse hereafter; the spend log is retained for "
                        "reconciliation. Settled buys are final — recovery is a "
                        "human decision from the log, not an unwind"}


def _rolling_day_start() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def _buy_view(record: dict) -> dict:
    return {"item": record["item"], "total_usdc": record["total_usdc"],
            "fees_usdc": record.get("fees_usdc"), "txid": record.get("txid"),
            "payment_id": record.get("payment_id")}
