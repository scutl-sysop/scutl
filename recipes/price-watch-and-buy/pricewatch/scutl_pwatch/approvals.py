"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet/pserv/capp approvals modules — the human
creates a consumable token file out of band, the op consumes it — with
one addition this recipe needs: the FIRST-BUY token is SCOPED.

set-target and revoke are gated the plain way (a token per op). The
first-buy gate is different: arming it means "I approve the agent buying
this item at this price", so the token pins (item, target_price) in its
contents. buy() consumes it only when the current target matches; a
target change writes a new target_price, the scope no longer matches, and
the stale token is refused (and cleared) rather than honored. That is
what makes "any target change re-arms the approval gate" true in code
instead of in a prompt.
"""

from __future__ import annotations

import json

from .state import StateDir

PLAIN_OPS = ("set-target", "revoke")


class ApprovalRequired(Exception):
    def __init__(self, op: str, detail: str = ""):
        msg = (f"op '{op}' requires human approval: create the token with "
               f"'pricewatch-approve {op}' (out of band), then retry")
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.op = op


def consume(state: StateDir, op: str) -> None:
    """Consume a plain per-op token (set-target, revoke)."""
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def check_first_buy(state: StateDir, item: str, target_price: str) -> None:
    """Verify a scoped first-buy token is armed for exactly this (item,
    target_price) WITHOUT consuming it. Called before the merchant settle so
    an unapproved first buy never reaches the rail; the token is consumed
    only after a successful settle (consume_first_buy), so a transient settle
    failure leaves the gate armed for the idempotent retry. A scope mismatch
    is a stale token — removed and treated as no approval, because the human
    approved a different line than the one about to be crossed."""
    token = state.approvals / "first-buy.token"
    if not token.exists():
        raise ApprovalRequired(
            "approve-first-buy",
            f"first purchase for '{item}' at {target_price} needs an armed gate")
    scope = json.loads(token.read_text())
    if scope.get("item") != item or scope.get("target_price") != str(target_price):
        token.unlink()
        raise ApprovalRequired(
            "approve-first-buy",
            f"the armed gate was scoped to {scope.get('item')}@"
            f"{scope.get('target_price')}, not {item}@{target_price} — "
            f"re-arm for the current target")


def consume_first_buy(state: StateDir) -> None:
    """Delete the first-buy token after a settle has succeeded. Idempotent:
    a missing token (already consumed) is fine."""
    token = state.approvals / "first-buy.token"
    if token.exists():
        token.unlink()


def arm_first_buy(state: StateDir, item: str, target_price: str) -> str:
    """The human's arming act: write the scoped first-buy token. This IS the
    approval, so it is not itself token-gated — it is the door, not a step
    behind it."""
    state.init()
    token = state.approvals / "first-buy.token"
    token.write_text(json.dumps({"item": item, "target_price": str(target_price)}))
    token.chmod(0o600)
    return str(token)


def grant(state: StateDir, op: str) -> str:
    """Test/CLI helper for the plain per-op tokens."""
    if op not in PLAIN_OPS:
        raise ValueError(f"unknown gated op '{op}' (valid: {', '.join(PLAIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
