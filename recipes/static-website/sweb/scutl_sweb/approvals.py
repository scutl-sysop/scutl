"""Human approval tokens for admin ops (manifest: setup ceiling-ratified).

Same mechanism as prov/pserv/signer: the human creates a consumable
token file out of band —
    sweb-approve <op>            (or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. configure (which decides
the price ceiling and subscription cap) and set-key are never callable
from bare model intent through the typed-tool surface. Deliberately NOT
here: destroy — but note sweb's destroy is NOT prov's ungated destroy:
it deletes the site's only copy, so its gate is the verified export
(core.destroy), a physical precondition rather than a consent token.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "set-key")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'sweb-approve {op}' (out of band), then retry"
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
