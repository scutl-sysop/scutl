"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet signer's, capp's, sift's, and herald's:
the human creates a consumable token file out of band —
    pulse-approve <op>           (or: touch approvals/<op>.token)
The op consumes (deletes) the token. One token, one operation.

Only ADMIN is gated here — configure (fixes the checks and every
window) and decommission. The daily loop (probe/digest/read) needs no
token, because its dishonesty directions are confined in code: the
computed table derives from the log, freshness and gaps are arithmetic,
and dedup keys bound repetition. Anomaly flags are NOT an admin op at
all: no agent-reachable op clears one — the human runs the
pulse-clear-flag helper, which is a separate entry point.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "decommission")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'pulse-approve {op}' (out of band), then retry"
        )
        self.op = op


def consume(state: StateDir, op: str) -> None:
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def grant(state: StateDir, op: str) -> str:
    if op not in ADMIN_OPS:
        raise ValueError(f"unknown gated op '{op}' (valid: {', '.join(ADMIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
