"""Human approval tokens for admin ops (manifest: setup policy-ratified).

Same mechanism as prov/pserv/sweb: the human creates a consumable token
file out of band —
    odom-approve <op>            (or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. configure (which decides
the allowlist, ceiling, horizon, floor, and domain cap) and set-key are
never callable from bare model intent through the typed-tool surface.
Deliberately NOT here: buy — its gate is the decision tree plus a quote
the task approved (a physical precondition, the sweb-destroy pattern),
and the prepaid balance bounds the blast radius regardless.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "set-key")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'odom-approve {op}' (out of band), then retry"
        )
        self.op = op


def consume(state: StateDir, op: str) -> None:
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def grant(state: StateDir, op: str) -> str:
    if op not in ADMIN_OPS:
        raise ValueError(f"unknown admin op '{op}' (valid: {', '.join(ADMIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
