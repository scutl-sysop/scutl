"""Human approval tokens for consented acts (manifest: invariants 3, 10).

Same mechanism as prov/pserv/sweb/odom/wing/silo: the human creates a
consumable token file out of band —
    keep-approve <op>            (or: touch approvals/<op>.token)
The gated op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. Gated here: configure
(the walls), provision (real spend: $15/mo accrues from that moment),
destructive-migration (DROP/TRUNCATE is silo's delete doctrine wearing
DDL), and teardown (the final dump is the last state the estate will
ever have). Deliberately NOT gated: status, non-destructive migrate,
dump, rehearse, report — keeping the estate's state protected and
PROVING it must never wait on a human, and every refusal those paths
need is a code wall, not this channel.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "provision", "destructive-migration", "teardown")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'keep-approve {op}' (out of band), then retry"
        )
        self.op = op


def consume(state: StateDir, op: str) -> None:
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def grant(state: StateDir, op: str) -> str:
    if op not in ADMIN_OPS:
        raise ValueError(f"unknown op '{op}' (valid: {', '.join(ADMIN_OPS)})")
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
