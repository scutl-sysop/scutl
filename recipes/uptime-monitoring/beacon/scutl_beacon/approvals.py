"""Human approval tokens for admin ops (manifest: consented acts).

Same mechanism as prov/pserv/sweb/odom/wing/silo/bell: the human
creates a consumable token file out of band —
    beacon-approve <op>          (or: touch approvals/<op>.token)
The admin op consumes (deletes) the token. One token, one operation.

Honest scope note carries over unchanged: this is an *out-of-band
consent channel*, not a cryptographic barrier. Gated here: configure
(the walls) and deregister (retiring a watch retires an OBLIGATION,
and the characteristic abuse is silencing the alarm by unwatching the
service — the manifest's alarm-silencing failure mode; pausing and
deleting the prober monitor happen only inside the gated deregister).
Deliberately NOT gated: register (it adds watched obligations, never
removes them; the cap wall brakes it), probe, verify, and report
(proving reachability must never wait on a human)."""

from __future__ import annotations

from .state import StateDir

ADMIN_OPS = ("configure", "deregister")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"admin op '{op}' requires human approval: create the token with "
            f"'beacon-approve {op}' (out of band), then retry"
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
