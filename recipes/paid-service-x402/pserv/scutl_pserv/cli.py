"""CLI surface. Two entry points:

  pserv          — the agent-facing typed tool (JSON in/out, one op per call)
  pserv-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes mirror the wallet signer's:
  2 not-configured · 3 decommissioned · 4 approval-required
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import Manager
from .state import Decommissioned, NotConfigured, StateDir


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pserv")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("stop")

    ep = sub.add_parser("earnings")
    ep.add_argument("--since", default=None, help="ISO timestamp")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("configure")
    cp.add_argument("--payto", required=True)
    cp.add_argument("--price", required=True)
    cp.add_argument("--offering", required=True)
    cp.add_argument("--bind-addr", default="127.0.0.1")
    cp.add_argument("--bind-port", type=int, default=8402)
    cp.add_argument("--resource-path", default=None)
    sp = asub.add_parser("set-payto")
    sp.add_argument("--payto", required=True)
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "start":
            out = manager.start()
        elif args.cmd == "stop":
            out = manager.stop()
        elif args.cmd == "earnings":
            out = manager.earnings(args.since)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(args.payto, Decimal(args.price), args.offering,
                                    args.bind_addr, args.bind_port, args.resource_path)
        elif args.cmd == "admin" and args.op == "set-payto":
            out = manager.set_payto(args.payto)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"service decommissioned at {e}; start refuses", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except (ValueError, RuntimeError) as e:
        _fail("invalid", str(e), 1)

    print(json.dumps(out))


def approve(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pserv-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "consumed_by": "next admin op"}))
