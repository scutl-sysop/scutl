"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet signer's, capp's, and sift's: the human
creates a consumable token file out of band —
    herald-approve <op>          (or: touch approvals/<op>.token)
The op consumes (deletes) the token. One token, one operation.

Only ADMIN is gated here — configure (fixes the owner id and every
ceiling) and decommission. The daily loop (send/fetch/read) needs no
token, because its dangerous directions are confined in code: the one
recipient is config, the ceilings bound volume, and dedup keys bound
repetition. Chat is never admin: an owner request over the channel to
change caps or decommission still lands here, on this token path.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "decommission")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'herald-approve {op}' (out of band), then retry"
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
