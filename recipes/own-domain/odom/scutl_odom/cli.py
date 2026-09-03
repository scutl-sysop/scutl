"""CLI surface. Two entry points:

  odom          — the agent-facing typed tool (JSON in/out, one op per call)
  odom-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/undetermined · 2 not-configured · 4 approval-required ·
  5 limit-refused (a code-enforced wall said no; never retried around) ·
  6 price-moved / stale-quote (a re-quote is a new decision, not a retry)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager, PriceMoved
from .network import (InsufficientFunds, PermanentError, TransientError)
from .state import NoApiKey, NotConfigured, StateDir

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
    p = _Parser(prog="odom")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("watch")

    qp = sub.add_parser("quote")
    qp.add_argument("domain")

    bp = sub.add_parser("buy")
    bp.add_argument("domain")
    bp.add_argument("--quote-id", required=True)

    rp = sub.add_parser("renew")
    rp.add_argument("domain")

    dp = sub.add_parser("delegate")
    dp.add_argument("domain")
    dp.add_argument("--ns-set", required=True)

    ep = sub.add_parser("export")
    ep.add_argument("domain")

    lp = sub.add_parser("log")
    lp.add_argument("--reconcile", action="store_true")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("configure")
    cp.add_argument("--tld-allowlist", required=True,
                    help="comma-separated, e.g. com,net,org")
    cp.add_argument("--ceiling-usd", required=True)
    cp.add_argument("--horizon-days", type=int, default=45)
    cp.add_argument("--floor-usd", default="20.00")
    cp.add_argument("--max-domains", type=int, default=1)
    cp.add_argument("--ns-sets", default=None,
                    help='JSON, e.g. {"estate": ["ns1.x", "ns2.x"]}')
    cp.add_argument("--no-auto-renew-backstop", action="store_true")
    kp = asub.add_parser("set-key")
    kp.add_argument("--key-file", required=True)

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "watch":
            out = manager.watch()
        elif args.cmd == "quote":
            out = manager.quote(args.domain)
        elif args.cmd == "buy":
            out = manager.buy(args.domain, args.quote_id)
        elif args.cmd == "renew":
            out = manager.renew(args.domain)
        elif args.cmd == "delegate":
            out = manager.delegate(args.domain, args.ns_set)
        elif args.cmd == "export":
            out = manager.export(args.domain)
        elif args.cmd == "log":
            out = manager.reconcile() if args.reconcile else manager.log()
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.tld_allowlist.split(","),
                Decimal(args.ceiling_usd), args.horizon_days,
                Decimal(args.floor_usd), args.max_domains,
                json.loads(args.ns_sets) if args.ns_sets else None,
                not args.no_auto_renew_backstop)
        elif args.cmd == "admin" and args.op == "set-key":
            out = manager.set_key(args.key_file)
        else:  # pragma: no cover
            _fail("usage", "unknown command", 1)
    except NotConfigured as e:
        _fail("not-configured", f"no config at {e}; run odom admin configure", 2)
    except NoApiKey as e:
        _fail("no-api-key", f"no key at {e}; run odom admin set-key", 2)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except PriceMoved as e:
        _fail("price-moved", str(e), 6)
    except InsufficientFunds as e:
        _fail("insufficient-funds",
              f"{e} — escalate to the owner; never loop a buy against an "
              f"empty balance", 5)
    except TransientError as e:
        _fail("transient", f"{e} — state may have changed; run "
                           f"'odom log --reconcile' BEFORE any retry "
                           f"(the idempotency key makes replay safe)", 1)
    except PermanentError as e:
        _fail("registrar-refused", str(e), 1)
    except ValueError as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="odom-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
