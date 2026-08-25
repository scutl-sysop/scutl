"""CLI surface. Two entry points:

  substew          — the agent-facing typed tool (JSON in/out, one op/call)
  substew-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes (manifest execute block):
  2 not-configured · 3 tombstoned · 4 approval-required ·
  5 re-consent-required (renewal parked above the consented price) ·
  6 merchant-transient (retry with the SAME payment id) ·
  7 moved-uphill (settle total exceeded the accepted quote — never accept) ·
  8 over-cap / limit-refused (fees, per-renewal or period cap) ·
  9 period-already-settled (double-billing — escalate with both charges) ·
  10 cancelled (post-cancel charge attempt — refused, recorded, escalate)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import (Cancelled, DoubleBilling, LimitRefused, Manager,
                   MovedUphill, ReConsentRequired)
from .network import PermanentError, TransientError
from .state import NotConfigured, StateDir, Tombstoned, UnknownQuote


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="substew")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("quote")

    rp = sub.add_parser("renew")
    rp.add_argument("--quote-id", required=True)
    rp.add_argument("--payment-id", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("consent")
    cp.add_argument("--service", required=True)
    cp.add_argument("--price", required=True, help="agreed price per period (USDC)")
    cp.add_argument("--period-days", default="30")
    cp.add_argument("--cap-per-renewal", help="defaults to the agreed price")
    cp.add_argument("--cap-period", required=True)
    cp.add_argument("--max-fees-pct", default="15")
    rcp = asub.add_parser("re-consent")
    rcp.add_argument("--price", required=True, help="the NEW price being consented to")
    asub.add_parser("cancel")
    asub.add_parser("revoke")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "quote":
            out = manager.quote()
        elif args.cmd == "renew":
            out = manager.renew(args.quote_id, args.payment_id)
        elif args.cmd == "admin" and args.op == "consent":
            price = Decimal(args.price)
            out = manager.consent(
                args.service, price, int(args.period_days),
                Decimal(args.cap_per_renewal) if args.cap_per_renewal else price,
                Decimal(args.cap_period), Decimal(args.max_fees_pct))
        elif args.cmd == "admin" and args.op == "re-consent":
            out = manager.re_consent(Decimal(args.price))
        elif args.cmd == "admin" and args.op == "cancel":
            out = manager.cancel()
        else:  # admin revoke
            out = manager.revoke()
    except NotConfigured as e:
        _fail("not-configured", f"no consent ({e}); run admin consent first", 2)
    except Tombstoned as e:
        _fail("tombstoned", f"revoked at {e}; renewals refuse (status still works)", 3)
    except UnknownQuote as e:
        _fail("unknown-quote", f"no live quote '{e}'; call quote first "
                               f"(a settled or expired quote id is gone)", 8)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except ReConsentRequired as e:
        _fail("re-consent-required", str(e), 5)
    except MovedUphill as e:
        _fail("moved-uphill", str(e), 7)
    except DoubleBilling as e:
        _fail("period-already-settled", str(e), 9)
    except Cancelled as e:
        _fail("cancelled", str(e), 10)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 8)
    except TransientError as e:
        _fail("merchant-transient", f"{e} — retry renew with the SAME payment id", 6)
    except PermanentError as e:
        _fail("merchant-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="substew-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    sub = p.add_subparsers(dest="op", required=True)
    for op in approvals.PLAIN_OPS:
        sub.add_parser(op)
    rc = sub.add_parser("re-consent")
    rc.add_argument("--price", required=True,
                    help="the exact NEW price being approved")

    args = p.parse_args(argv)
    state = StateDir()
    if args.op == "re-consent":
        token = approvals.arm_re_consent(state, args.price)
        out = {"armed": "re-consent", "price": args.price, "token": token,
               "note": "scoped to this price; consumed by the next matching "
                       "admin re-consent"}
    else:
        token = approvals.grant(state, args.op)
        out = {"granted": args.op, "token": token,
               "note": "consumed (deleted) by the next matching op"}
    print(json.dumps(out))
