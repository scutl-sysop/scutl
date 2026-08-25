"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet/pserv/capp/pwatch approvals modules — the
human creates a consumable token file out of band, the op consumes it —
with the scoping this recipe needs: the RE-CONSENT token pins the new
price. Consenting to "the increase" in the abstract does not exist; the
human approves a number, and the admin op consumes the token only when
the number matches. A hike that arrives after the human granted a
re-consent for a smaller hike is a fresh ask, not a covered one.

consent, cancel and revoke are gated the plain way (a token per op).
"""

from __future__ import annotations

import json

from .state import StateDir

PLAIN_OPS = ("consent", "cancel", "revoke")


class ApprovalRequired(Exception):
    def __init__(self, op: str, detail: str = ""):
        msg = (f"op '{op}' requires human approval: create the token with "
               f"'substew-approve {op}' (out of band), then retry")
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.op = op


def consume(state: StateDir, op: str) -> None:
    """Consume a plain per-op token (consent, cancel, revoke)."""
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def consume_re_consent(state: StateDir, new_price: str) -> None:
    """Consume the scoped re-consent token, only if the human approved
    exactly this price. A scope mismatch is a stale token — removed and
    treated as no approval, because the human approved a different number
    than the one about to become the line."""
    token = state.approvals / "re-consent.token"
    if not token.exists():
        raise ApprovalRequired(
            "re-consent",
            f"raising the consented price to {new_price} needs a human token "
            f"scoped to that price: 'substew-approve re-consent --price "
            f"{new_price}'")
    scope = json.loads(token.read_text())
    if scope.get("price") != str(new_price):
        token.unlink()
        raise ApprovalRequired(
            "re-consent",
            f"the granted token was scoped to price {scope.get('price')}, "
            f"not {new_price} — re-approve for the current ask")
    token.unlink()


def arm_re_consent(state: StateDir, new_price: str) -> str:
    """The human's approval act for a price change: write the scoped token.
    This IS the approval, so it is not itself token-gated."""
    state.init()
    token = state.approvals / "re-consent.token"
    token.write_text(json.dumps({"price": str(new_price)}))
    token.chmod(0o600)
    return str(token)


def grant(state: StateDir, op: str) -> str:
    """Human/CLI helper for the plain per-op tokens."""
    if op not in PLAIN_OPS:
        raise ValueError(f"unknown gated op '{op}' (valid: {', '.join(PLAIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
