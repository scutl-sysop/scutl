"""Human approval tokens for gated ops (manifest: approval: human).

Same mechanism as the wallet signer's, pserv's, and prov's approvals
modules: the human creates a consumable token file out of band —
    capp-approve <op>            (or: touch approvals/<op>.token)
The op consumes (deletes) the token. One token, one operation.

What is gated here differs from prov in one deliberate way: PURCHASE
itself is an approved op. On the provision rail the recurring spend was
the instance and destroy was the safe direction; here every unit of
spend is a discrete purchase, so money-out is the gated direction and
the safe direction — refusing to buy, reading status — needs no token
and never will. This is also what makes renewal honest: a re-purchase
after quota exhaustion or a price change is a fresh consent, not an
inherited one.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "purchase", "decommission")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'capp-approve {op}' (out of band), then retry"
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
