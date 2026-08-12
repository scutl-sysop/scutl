"""Human approval tokens for admin ops (manifest: approval: human).

Mechanism (v1): the human creates a consumable token file out of band —
    signer-approve <op>          (tiny helper, or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note: this is an *out-of-band consent channel*, not a
cryptographic barrier — an agent with unrestricted shell on the same uid
could fabricate a token. Deployments that need a hard boundary run the
signer under a separate uid owning state_dir. The manifest invariant is
that wallet_admin is never callable from bare model intent through the
typed-tool surface, which this satisfies.
"""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("keygen", "revoke")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'signer-approve {op}' (out of band), then retry"
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
