"""Capability-purchase core: the guardrail component of recipe #4.

Manifest invariants enforced HERE, in code (recipe.yaml components.capp):
  - no purchase leaves the box unless the plan is allowlisted, its listed
    price is within max_purchase_usd, AND a human approval token for
    'purchase' exists — every purchase is a fresh consent
  - the purchase is logged before the response returns; the vendor-issued
    API key goes straight from the purchase response to disk (0600) and
    never appears in any return value, log record, or error message
  - metering happens locally: a call is refused in code once the local
    counter reaches the purchased quota — quota exhaustion is a report to
    the human, never an automatic re-purchase
  - the local counter and the vendor's counter are compared on status;
    disagreement is reported with both numbers, never silently adopted
  - status (reading the record) is never gated: it works before
    configure, after decommission, and without a key
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from . import approvals
from .network import VendorClient
from .state import StateDir


class LimitRefused(Exception):
    """A code-enforced limit said no. Exit 5; never retried around."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 client: VendorClient | None = None):
        self.state = state or StateDir()
        self.client = client or VendorClient(self.state)

    # -- introspection: never gated -------------------------------------
    def status(self) -> dict:
        key_present = self.state.api_key_file.exists()
        try:
            config = self.state.load_config()
        except Exception:
            config = None
        out: dict = {
            "configured": config is not None,
            "key_present": key_present,
            "decommissioned": self.state.decommission_marker.exists(),
        }
        if config:
            out["limits"] = {
                "plans": config["plans"],
                "max_purchase_usd": config["max_purchase_usd"],
            }
        current = self.state.current_purchase()
        if current:
            local_used = self.state.local_used()
            out["plan"] = {
                "purchase_id": current["purchase_id"],
                "plan": current["plan"],
                "price_usd": current["price_usd"],
                "quota_calls": current["quota_calls"],
                "local_used": local_used,
                "local_remaining": max(0, current["quota_calls"] - local_used),
            }
        if config:
            out.update(self._reconcile(current, key_present))
        return out

    def _reconcile(self, current: dict | None, key_present: bool) -> dict:
        """Our ledger vs the vendor's, both axes: purchases (ack-lost
        evidence — deliberately NOT gated on a key, because the ack-lost
        case is exactly the one where the key never arrived) and usage
        (metering disagreement). Reported, never silently adopted."""
        vendor_purchases = {p["purchase_id"]: p for p in self.client.purchases()}
        log_ids = self.state.log_purchase_ids()
        out: dict = {
            "vendor_reachable": True,
            "foreign_purchases": sorted(set(vendor_purchases) - log_ids),
            "lost_purchases": sorted(log_ids - set(vendor_purchases)),
        }
        if current and key_present:
            local_used = self.state.local_used()
            try:
                vendor_usage = self.client.usage(self.state.load_api_key())
            except Exception as e:
                # status is never gated: a vendor that refuses the usage
                # probe (revoked key, outage) is itself a finding to
                # report, not a reason for status to fail.
                out["usage"] = {"local_used": local_used,
                                "quota_calls": current["quota_calls"],
                                "vendor_error": str(e),
                                "disagreement": True}
                return out
            out["usage"] = {
                "local_used": local_used,
                "vendor_used": vendor_usage.get("used"),
                "quota_calls": current["quota_calls"],
                "disagreement": vendor_usage.get("used") != local_used,
            }
        return out

    # -- purchase: every limit checked before money moves ----------------
    def purchase(self, plan_id: str) -> dict:
        self.state.check_not_decommissioned()
        config = self.state.load_config()
        if plan_id not in config["plans"]:
            raise LimitRefused(
                f"plan '{plan_id}' not in allowlist {config['plans']}")
        listed = self._plan_price(plan_id)
        ceiling = Decimal(config["max_purchase_usd"])
        if listed > ceiling:
            raise LimitRefused(
                f"plan '{plan_id}' listed at {listed} USD, over "
                f"max_purchase_usd {ceiling}")
        # Fresh consent per purchase: the token is consumed BEFORE the
        # vendor call, so an unapproved purchase never reaches the rail.
        approvals.consume(self.state, "purchase")
        result = self.client.purchase(plan_id)
        # The one response that carries the secret: key to disk first,
        # then strip it from everything that flows onward.
        key = result.pop("api_key", None)
        if key:
            self.state.write_secret(key.encode())
        charged = Decimal(str(result["price_usd"]))
        if charged > ceiling:
            # Defense in depth: a vendor that charges above the listed
            # price is a disagreement to escalate; the record must show
            # what actually happened.
            self.state.append_usage_event({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "purchased",
                "purchase_id": result["purchase_id"],
                "plan": plan_id,
                "price_usd": str(charged),
                "quota_calls": int(result["quota_calls"]),
                "over_ceiling": True,
            })
            raise LimitRefused(
                f"vendor charged {charged} USD for '{plan_id}' (listed "
                f"{listed}), over ceiling {ceiling} — purchase recorded, "
                f"escalate to the human")
        self.state.append_usage_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "purchased",
            "purchase_id": result["purchase_id"],
            "plan": plan_id,
            "price_usd": str(charged),
            "quota_calls": int(result["quota_calls"]),
        })
        return {
            "purchased": True,
            "purchase_id": result["purchase_id"],
            "plan": plan_id,
            "price_usd": str(charged),
            "quota_calls": int(result["quota_calls"]),
            "key_present": key is not None,
        }

    def _plan_price(self, plan_id: str) -> Decimal:
        for p in self.client.plans():
            if p["id"] == plan_id:
                return Decimal(str(p["price_usd"]))
        raise LimitRefused(f"plan '{plan_id}' unknown to the vendor; refusing")

    # -- metered use: refused in code at the local quota ------------------
    def call(self, query: str) -> dict:
        self.state.check_not_decommissioned()
        self.state.load_config()
        current = self.state.current_purchase()
        if current is None:
            raise LimitRefused("no plan purchased; capp purchase is a "
                               "human-approved op")
        local_used = self.state.local_used()
        if local_used >= current["quota_calls"]:
            raise LimitRefused(
                f"quota exhausted: {local_used}/{current['quota_calls']} "
                f"calls used on plan '{current['plan']}'. A new purchase "
                f"is a fresh human-approved consent — report, do not buy")
        key = self.state.load_api_key()
        result = self.client.call(key, query)
        # Log after vendor success, before returning: an unlogged
        # successful call would make our counter run behind the bill.
        self.state.append_usage_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "call",
            "purchase_id": current["purchase_id"],
        })
        return {"data": result,
                "local_used": local_used + 1,
                "local_remaining": current["quota_calls"] - local_used - 1}

    # -- admin (human-approved) ----------------------------------------
    def configure(self, plans: list[str], max_purchase_usd: Decimal) -> dict:
        approvals.consume(self.state, "configure")
        if max_purchase_usd <= 0:
            raise ValueError("max_purchase_usd must be > 0")
        if not plans:
            raise ValueError("plan allowlist must not be empty")
        self.state.init()
        config = {"plans": list(plans),
                  "max_purchase_usd": str(max_purchase_usd)}
        self.state.save_config(config)
        return {"configured": True, **config}

    def decommission(self) -> dict:
        """Allowed any time: a prepaid quota is sunk cost, not running
        spend, so nothing has to be torn down first. purchase and call
        refuse thereafter; status keeps working."""
        approvals.consume(self.state, "decommission")
        self.state.load_config()
        import json
        marker = {"decommissioned_at": datetime.now(timezone.utc).isoformat()}
        self.state.decommission_marker.write_text(json.dumps(marker))
        return {**marker,
                "note": "API key revocation happens vendor-side, by the "
                        "human — this marker is not revocation"}
