"""CLI surface. Two entry points:

  pricewatch          — the agent-facing typed tool (JSON in/out, one op/call)
  pricewatch-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes (manifest execute block):
  2 not-configured · 3 tombstoned · 4 approval-required ·
  5 over-target/cap (a code-enforced limit said no) ·
  6 merchant-transient (retry with the SAME payment id) ·
  7 moved-uphill (checkout total exceeded the accepted quote — never accept)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager, MovedUphill
from .network import PermanentError, TransientError
from .state import NotConfigured, StateDir, Tombstoned, UnknownQuote

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
    p = _Parser(prog="pricewatch")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    qp = sub.add_parser("quote")
    qp.add_argument("item")

    bp = sub.add_parser("buy")
    bp.add_argument("--quote-id", required=True)
    bp.add_argument("--payment-id", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    stp = asub.add_parser("set-target")
    stp.add_argument("--item", required=True)
    stp.add_argument("--price", required=True, help="target price (USDC)")
    stp.add_argument("--cap-per-buy", help="defaults to target price")
    stp.add_argument("--cap-daily", required=True)
    stp.add_argument("--max-fees-pct", default="15")
    afp = asub.add_parser("approve-first-buy")
    afp.add_argument("--item", required=True)
    asub.add_parser("revoke")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "quote":
            out = manager.quote(args.item)
        elif args.cmd == "buy":
            out = manager.buy(args.quote_id, args.payment_id)
        elif args.cmd == "admin" and args.op == "set-target":
            price = Decimal(args.price)
            out = manager.set_target(
                args.item, price,
                Decimal(args.cap_per_buy) if args.cap_per_buy else price,
                Decimal(args.cap_daily), Decimal(args.max_fees_pct))
        elif args.cmd == "admin" and args.op == "approve-first-buy":
            out = manager.approve_first_buy(args.item)
        else:  # admin revoke
            out = manager.revoke()
    except NotConfigured as e:
        _fail("not-configured", f"no target ({e}); run admin set-target first", 2)
    except Tombstoned as e:
        _fail("tombstoned", f"revoked at {e}; buys refuse (status still works)", 3)
    except UnknownQuote as e:
        _fail("unknown-quote", f"no live quote '{e}'; call quote first "
                               f"(a settled or expired quote id is gone)", 5)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except MovedUphill as e:
        _fail("moved-uphill", str(e), 7)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except TransientError as e:
        _fail("merchant-transient", f"{e} — retry buy with the SAME payment id", 6)
    except PermanentError as e:
        _fail("merchant-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="pricewatch-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    sub = p.add_subparsers(dest="op", required=True)
    for op in approvals.PLAIN_OPS:
        sub.add_parser(op)
    fb = sub.add_parser("first-buy")
    fb.add_argument("--item", required=True)

    args = p.parse_args(argv)
    state = StateDir()
    if args.op == "first-buy":
        # scoped to the current target price — set-target must have run
        token = approvals.arm_first_buy(
            state, args.item, state.load_target()["target_price"])
        out = {"armed": "first-buy", "item": args.item, "token": token}
    else:
        token = approvals.grant(state, args.op)
        out = {"granted": args.op, "token": token,
               "note": "consumed (deleted) by the next matching op"}
    print(json.dumps(out))
