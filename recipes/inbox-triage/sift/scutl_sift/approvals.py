"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet signer's, pserv's, prov's, and capp's: the
human creates a consumable token file out of band —
    sift-approve <op>            (or: touch approvals/<op>.token)
The op consumes (deletes) the token. One token, one operation.

Only ADMIN is gated here — configure (fixes the cap and categories) and
decommission. The daily loop (fetch/read/triage/draft) needs no token,
because the component cannot ACT on mail: no send path exists, drafts
are confined to the replied-to sender, and the fetch cap bounds volume.
The safe direction never needs approval friction; the dangerous
direction does not exist.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "decommission")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'sift-approve {op}' (out of band), then retry"
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
