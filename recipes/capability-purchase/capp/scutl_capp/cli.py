"""CLI surface. Two entry points:

  capp          — the agent-facing typed tool (JSON in/out, one op per call)
  capp-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes reuse prov's taxonomy:
  2 not-configured · 3 decommissioned · 4 approval-required ·
  5 limit-refused (a code-enforced limit said no; never retried around)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager
from .network import PermanentError, TransientError
from .state import Decommissioned, NoApiKey, NotConfigured, StateDir

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
    p = _Parser(prog="capp")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    bp = sub.add_parser("purchase")
    bp.add_argument("--plan", required=True)

    cp = sub.add_parser("call")
    cp.add_argument("--query", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--plans", required=True, help="comma-separated plan ids")
    gp.add_argument("--max-purchase", required=True,
                    help="USD ceiling per purchase")
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "purchase":
            out = manager.purchase(args.plan)
        elif args.cmd == "call":
            out = manager.call(args.query)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(args.plans.split(","),
                                    Decimal(args.max_purchase))
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except NoApiKey as e:
        _fail("not-configured", f"missing API key ({e}); a successful "
                                f"purchase writes it", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"decommissioned at {e}; purchase/call "
                                f"refuse (status still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except TransientError as e:
        _fail("vendor-transient", f"{e} — check capp status before any retry", 1)
    except PermanentError as e:
        _fail("vendor-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="capp-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching op"}))
