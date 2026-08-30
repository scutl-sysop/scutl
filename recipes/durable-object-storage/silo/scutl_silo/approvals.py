"""Human approval tokens for admin ops (manifest: consented acts).

Same mechanism as prov/pserv/sweb/odom/wing: the human creates a
consumable token file out of band —
    silo-approve <op>            (or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. Gated here: configure
(the walls), provision (real spend accrues from that moment), delete
(removing a backup is the highest-stakes write in the recipe), and
teardown (everything dies at once). Deliberately NOT gated: put, get,
rehearse, inventory, report — backing up and PROVING the backup must
never wait on a human, and an over-cap put is refused by the cap wall,
not by this channel.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "provision", "delete", "teardown")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'silo-approve {op}' (out of band), then retry"
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
