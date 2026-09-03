"""CLI surface. Two entry points:

  refclerk          — the agent-facing typed tool (JSON in/out, one op/call)
  refclerk-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes (manifest execute block):
  2 not-configured · 3 approval-required ·
  4 no-matching-settle (the claimed charge never happened — refuse) ·
  5 exception-required (verified but outside policy — parked for a human) ·
  6 settlement-transient (retry with the SAME refund id) ·
  7 over-amount (claimed exceeds the settled/refundable amount) ·
  8 over-cap / limit-refused (period cap, denied claim, unknown claim) ·
  9 already-refunded (double-refund — escalate with the prior refund) ·
  10 decommissioned (post-decommission refund attempt — refused, recorded)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import (AlreadyRefunded, Decommissioned, ExceptionRequired,
                   LimitRefused, Manager, NoMatchingSettle, OverAmount)
from .network import PermanentError, TransientError
from .state import NotConfigured, StateDir, Tombstoned, UnknownClaim

class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 ('invalid'), never argparse's default 2 —
    2 is the taxonomy's not-configured and an agent following the
    protocol would misread a typo as 'run setup first' (cst-qiru)."""
    def error(self, message):
        self.print_usage(__import__("sys").stderr)
        print(f"{self.prog}: error: {message}",
              file=__import__("sys").stderr)
        raise SystemExit(1)



def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = _Parser(prog="refclerk")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("claim")

    vp = sub.add_parser("verify")
    vp.add_argument("--claim-id", required=True)

    rp = sub.add_parser("refund")
    rp.add_argument("--claim-id", required=True)
    rp.add_argument("--refund-id", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("configure")
    cp.add_argument("--window-days", default="30")
    cp.add_argument("--auto-max", required=True,
                    help="largest single refund settled unattended (USDC)")
    cp.add_argument("--period-cap", required=True,
                    help="max total refunded per rolling period (USDC)")
    cp.add_argument("--earnings-log", required=True,
                    help="path to the merchant's earnings.log (read-only)")
    cp.add_argument("--period-days", default="30")
    ep = asub.add_parser("exception")
    ep.add_argument("--claim-id", required=True)
    dp = asub.add_parser("deny")
    dp.add_argument("--claim-id", required=True)
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "claim":
            out = manager.claim()
        elif args.cmd == "verify":
            out = manager.verify(args.claim_id)
        elif args.cmd == "refund":
            out = manager.refund(args.claim_id, args.refund_id)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(int(args.window_days),
                                    Decimal(args.auto_max),
                                    Decimal(args.period_cap),
                                    args.earnings_log,
                                    int(args.period_days))
        elif args.cmd == "admin" and args.op == "exception":
            out = manager.exception(args.claim_id)
        elif args.cmd == "admin" and args.op == "deny":
            out = manager.deny(args.claim_id)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"no policy ({e}); run admin configure first", 2)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 3)
    except NoMatchingSettle as e:
        _fail("no-matching-settle", str(e), 4)
    except ExceptionRequired as e:
        _fail("exception-required", str(e), 5)
    except TransientError as e:
        _fail("settlement-transient", f"{e} — retry refund with the SAME refund id", 6)
    except OverAmount as e:
        _fail("over-amount", str(e), 7)
    except AlreadyRefunded as e:
        _fail("already-refunded", str(e), 9)
    except Decommissioned as e:
        _fail("decommissioned", str(e), 10)
    except Tombstoned as e:
        _fail("decommissioned", f"decommissioned at {e}; refunds refuse "
                                f"(status still works)", 10)
    except UnknownClaim as e:
        _fail("unknown-claim", f"no fetched claim '{e}'; call claim first", 8)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 8)
    except PermanentError as e:
        _fail("settlement-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="refclerk-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    sub = p.add_subparsers(dest="op", required=True)
    for op in approvals.PLAIN_OPS:
        sub.add_parser(op)
    for op in approvals.SCOPED_OPS:
        sp = sub.add_parser(op)
        sp.add_argument("--claim-id", required=True,
                        help="the exact claim being decided")

    args = p.parse_args(argv)
    state = StateDir()
    if args.op in approvals.SCOPED_OPS:
        token = approvals.arm_scoped(state, args.op, args.claim_id)
        out = {"armed": args.op, "claim_id": args.claim_id, "token": token,
               "note": "scoped to this claim; consumed by the next matching "
                       "admin op"}
    else:
        token = approvals.grant(state, args.op)
        out = {"granted": args.op, "token": token,
               "note": "consumed (deleted) by the next matching op"}
    print(json.dumps(out))
