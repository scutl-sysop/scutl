"""Human approval tokens for admin ops (manifest: gpu_admin).

Same mechanism as prov/silo/pserv: the human creates a consumable token
file out of band —
    gpod-approve <op>            (or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. Gated: configure (the
walls ARE the spend policy), set-key, decommission. Deliberately NOT
here, and never will be: destroy and destroy-all — the manifest's
loudest invariant is that the safe direction is never gated; no
approval token, no decommission marker, no config error may leave a
reachable pod running at ten times prov's hourly rate.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "set-key", "decommission")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'gpod-approve {op}' (out of band), then retry"
        )
        self.op = op


def consume(state: StateDir, op: str) -> None:
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def grant(state: StateDir, op: str) -> str:
    if op not in ADMIN_OPS:
        raise ValueError(
            f"unknown admin op '{op}' (valid: {', '.join(ADMIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
