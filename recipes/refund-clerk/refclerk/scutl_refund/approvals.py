"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet/pserv/pwatch/substew approvals modules — the
human creates a consumable token file out of band, the op consumes it —
with the scoping this recipe needs: the EXCEPTION and DENY tokens pin the
claim id. Approving "an exception" in the abstract does not exist; the
human decides one parked claim, and the admin op consumes the token only
when the claim id matches. A different parked claim is a fresh ask, not
a covered one.

configure and decommission are gated the plain way (a token per op).
"""

from __future__ import annotations

import json

from .state import StateDir

PLAIN_OPS = ("configure", "decommission")
SCOPED_OPS = ("exception", "deny")


class ApprovalRequired(Exception):
    def __init__(self, op: str, detail: str = ""):
        msg = (f"op '{op}' requires human approval: create the token with "
               f"'refclerk-approve {op}' (out of band), then retry")
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.op = op


def consume(state: StateDir, op: str) -> None:
    """Consume a plain per-op token (configure, decommission)."""
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def consume_scoped(state: StateDir, op: str, claim_id: str) -> None:
    """Consume an exception/deny token, only if the human decided exactly
    this claim. A scope mismatch is a stale token — removed and treated as
    no approval, because the human ruled on a different claim than the one
    about to move money (or close unrefunded)."""
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(
            op,
            f"deciding claim {claim_id} needs a human token scoped to it: "
            f"'refclerk-approve {op} --claim-id {claim_id}'")
    scope = json.loads(token.read_text())
    if scope.get("claim_id") != claim_id:
        token.unlink()
        raise ApprovalRequired(
            op,
            f"the granted token was scoped to claim {scope.get('claim_id')}, "
            f"not {claim_id} — re-approve for the current claim")
    token.unlink()


def arm_scoped(state: StateDir, op: str, claim_id: str) -> str:
    """The human's decision act for one parked claim: write the scoped
    token. This IS the approval, so it is not itself token-gated."""
    if op not in SCOPED_OPS:
        raise ValueError(f"unknown scoped op '{op}' (valid: {', '.join(SCOPED_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.write_text(json.dumps({"claim_id": claim_id}))
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
