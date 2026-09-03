"""CLI surface. Two entry points:

  sweb          — the agent-facing typed tool (JSON in/out, one op per call)
  sweb-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/undetermined · 2 not-configured · 4 approval-required ·
  5 limit-refused (a code-enforced wall said no; never retried around) ·
  6 duplicate publish id (reconcile, never blind re-publish)
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import DuplicatePublish, LimitRefused, Manager
from .network import PermanentError, TransientError
from .state import NoApiKey, NotConfigured, NotProvisioned, StateDir

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
    p = _Parser(prog="sweb")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    pp = sub.add_parser("provision")
    pp.add_argument("--cluster", required=True, type=int)

    bp = sub.add_parser("publish")
    bp.add_argument("--publish-id", required=True)
    bp.add_argument("--source", required=True)

    sub.add_parser("verify")
    sub.add_parser("rotate")
    sub.add_parser("edge-attach")
    sub.add_parser("edge-status")

    lp = sub.add_parser("log")
    lp.add_argument("--reconcile", action="store_true")

    dp = sub.add_parser("destroy")
    dp.add_argument("--export", required=True, dest="export_dir")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("configure")
    cp.add_argument("--ceiling-usd", required=True)
    cp.add_argument("--max-subscriptions", type=int, default=1)
    cp.add_argument("--site-bucket", required=True)
    cp.add_argument("--serving", default="provider-domain")
    cp.add_argument("--site-name", default=None)
    kp = asub.add_parser("set-key")
    kp.add_argument("--key-file", required=True)

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "provision":
            out = manager.provision(args.cluster)
        elif args.cmd == "publish":
            out = manager.publish(args.publish_id, args.source)
        elif args.cmd == "verify":
            out = manager.verify()
        elif args.cmd == "rotate":
            out = manager.rotate()
        elif args.cmd == "edge-attach":
            out = manager.edge_attach()
        elif args.cmd == "edge-status":
            out = manager.edge_status()
        elif args.cmd == "log":
            out = manager.reconcile() if args.reconcile else manager.log()
        elif args.cmd == "destroy":
            out = manager.destroy(args.export_dir)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(Decimal(args.ceiling_usd),
                                    args.max_subscriptions,
                                    args.site_bucket, args.serving,
                                    args.site_name)
        elif args.cmd == "admin" and args.op == "set-key":
            out = manager.set_key(args.key_file)
        else:  # pragma: no cover
            _fail("usage", "unknown command", 1)
    except NotConfigured as e:
        _fail("not-configured", f"no config at {e}; run sweb admin configure", 2)
    except NoApiKey as e:
        _fail("no-api-key", f"no key at {e}; run sweb admin set-key", 2)
    except NotProvisioned as e:
        _fail("not-provisioned", f"no subscription ({e}); run sweb provision", 2)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except DuplicatePublish as e:
        _fail("duplicate-publish", str(e), 6)
    except TransientError as e:
        _fail("transient", f"{e} — state may have changed; run "
                           f"'sweb log --reconcile' BEFORE any retry", 1)
    except PermanentError as e:
        _fail("provider-refused", str(e), 1)
    except ValueError as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="sweb-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
