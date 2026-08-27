"""Human approval tokens for the custody layer's gated ops.

Same out-of-band-consent mechanism as the signer/substew approvals
modules (a consumable token file the human creates; an agent with
unrestricted same-uid shell could fabricate one — deployments needing a
hard boundary run msigner under its own uid). What this recipe adds is
SCOPE, in two places where the number/destination is the decision:

  ratchet  — the token pins (cap, to-amount). Approving "raise the daily
             cap" in the abstract does not exist; the human approves ONE
             cap moving to ONE number. A mismatched ask is a stale token:
             removed and treated as no approval.
  sweep    — the token pins the destination address the human TYPED, and
             the phase ('micro' or 'remainder'). The remainder phase
             needs a FRESH token: confirming the micro arrived is
             expressed by granting the second approval, not by anything
             the agent can do alone.

Plain (unscoped) tokens gate: keygen and revoke (consumed by the inner
signer, same files), backup-verify, restore-rehearsal, unpanic.
mw_panic is deliberately ungated (ratified 2026-08-27, cst-3ewh).
"""

from __future__ import annotations

import json

from scutl_signer.state import StateDir

PLAIN_OPS = ("backup-verify", "restore-rehearsal", "unpanic")
INNER_OPS = ("keygen", "revoke")          # granted here, consumed by scutl_signer


class ApprovalRequired(Exception):
    def __init__(self, op: str, detail: str = ""):
        msg = (f"admin op '{op}' requires human approval: create the token "
               f"with 'msigner-approve {op}' (out of band), then retry")
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.op = op


def consume(wstate: StateDir, op: str) -> None:
    token = wstate.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op)
    token.unlink()


def consume_ratchet(wstate: StateDir, cap: str, to_amount: str) -> None:
    token = wstate.approvals / "ratchet.token"
    if not token.exists():
        raise ApprovalRequired(
            "ratchet",
            f"moving {cap} to {to_amount} needs a token scoped to exactly "
            f"that: 'msigner-approve ratchet --cap {cap} --to {to_amount}'")
    scope = json.loads(token.read_text())
    if scope.get("cap") != cap or scope.get("to") != str(to_amount):
        token.unlink()
        raise ApprovalRequired(
            "ratchet",
            f"granted token was scoped to {scope.get('cap')} -> "
            f"{scope.get('to')}, not {cap} -> {to_amount}; re-approve for "
            f"the current ask")
    token.unlink()


def consume_sweep(wstate: StateDir, to_address: str, phase: str) -> None:
    token = wstate.approvals / "sweep.token"
    if not token.exists():
        raise ApprovalRequired(
            "sweep",
            f"the {phase} sweep authorization to {to_address} needs a token "
            f"scoped to that destination and phase: 'msigner-approve sweep "
            f"--to {to_address}" +
            (" --remainder'" if phase == "remainder" else "'"))
    scope = json.loads(token.read_text())
    if (scope.get("to", "").lower() != to_address.lower()
            or scope.get("phase") != phase):
        token.unlink()
        raise ApprovalRequired(
            "sweep",
            f"granted token was scoped to {scope.get('phase')} -> "
            f"{scope.get('to')}, not {phase} -> {to_address}; the human "
            f"types the destination at approval time — re-approve")
    token.unlink()


# -- the human's granting side (msigner-approve) --------------------------

def grant(wstate: StateDir, op: str) -> str:
    if op in INNER_OPS:
        from scutl_signer import approvals as inner
        return inner.grant(wstate, op)
    if op not in PLAIN_OPS:
        raise ValueError(
            f"unknown gated op '{op}' (valid: "
            f"{', '.join(INNER_OPS + PLAIN_OPS)}, ratchet, sweep)")
    wstate.init()
    token = wstate.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)


def grant_ratchet(wstate: StateDir, cap: str, to_amount: str) -> str:
    wstate.init()
    token = wstate.approvals / "ratchet.token"
    token.write_text(json.dumps({"cap": cap, "to": str(to_amount)}))
    token.chmod(0o600)
    return str(token)


def grant_sweep(wstate: StateDir, to_address: str, remainder: bool) -> str:
    wstate.init()
    token = wstate.approvals / "sweep.token"
    token.write_text(json.dumps({
        "to": to_address, "phase": "remainder" if remainder else "micro"}))
    token.chmod(0o600)
    return str(token)
