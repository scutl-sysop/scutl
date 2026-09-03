"""CLI surface. Two entry points:

  prov          — the agent-facing typed tool (JSON in/out, one op per call)
  prov-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes extend pserv's by one:
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
    p = _Parser(prog="prov")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("list")

    cp = sub.add_parser("create")
    cp.add_argument("--plan", required=True)
    cp.add_argument("--region", required=True)
    cp.add_argument("--label", required=True)
    cp.add_argument("--ssh-pubkey-file", default=None,
                    help="PUBLIC key file to inject via cloud-init")

    dp = sub.add_parser("destroy")
    dp.add_argument("--id", required=True, dest="instance_id")

    sub.add_parser("destroy-all")

    np = sub.add_parser("dns")
    nsub = np.add_subparsers(dest="op", required=True)
    sp = nsub.add_parser("set")
    sp.add_argument("--name", required=True)
    sp.add_argument("--type", required=True, dest="rtype")
    sp.add_argument("--value", required=True)
    xp = nsub.add_parser("delete")
    xp.add_argument("--name", required=True)
    xp.add_argument("--type", required=True, dest="rtype")
    nsub.add_parser("list")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--plans", required=True, help="comma-separated plan ids")
    gp.add_argument("--regions", required=True, help="comma-separated region ids")
    gp.add_argument("--max-instances", type=int, required=True)
    gp.add_argument("--max-hourly", required=True, help="USD ceiling per instance-hour")
    gp.add_argument("--dns-subzone", default=None)
    kp = asub.add_parser("set-key")
    kp.add_argument("--key-file", required=True)
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
            if not out.get("configured"):
                # taxonomy: pre-configure status is exit 2, matching
                # recipe.yaml setup.install and the sibling components
                # (found by the ADAPT fresh-agent run, cst-q03b)
                print(json.dumps(out))
                sys.exit(2)
        elif args.cmd == "list":
            out = manager.list()
        elif args.cmd == "create":
            out = manager.create(args.plan, args.region, args.label,
                                 args.ssh_pubkey_file)
        elif args.cmd == "destroy":
            out = manager.destroy(args.instance_id)
        elif args.cmd == "destroy-all":
            out = manager.destroy_all()
        elif args.cmd == "dns" and args.op == "set":
            out = manager.dns_set(args.name, args.rtype, args.value)
        elif args.cmd == "dns" and args.op == "delete":
            out = manager.dns_delete(args.name, args.rtype)
        elif args.cmd == "dns":  # list
            out = manager.dns_list()
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.plans.split(","), args.regions.split(","),
                args.max_instances, Decimal(args.max_hourly),
                args.dns_subzone)
        elif args.cmd == "admin" and args.op == "set-key":
            out = manager.set_key(args.key_file)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except NoApiKey as e:
        _fail("not-configured", f"missing API key ({e}); run admin set-key first", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"decommissioned at {e}; create/dns refuse "
                                f"(destroy still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except TransientError as e:
        _fail("provider-transient", f"{e} — safe to retry after a pause", 1)
    except PermanentError as e:
        _fail("provider-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="prov-approve",
        description="HUMAN helper: grant a one-shot approval token for an admin op",
    )
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching admin op"}))
