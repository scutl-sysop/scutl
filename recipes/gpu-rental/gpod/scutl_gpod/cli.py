"""CLI surface. Two entry points:

  gpod          — the agent-facing typed tool (JSON in/out, one op per call)
  gpod-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on
stderr with a nonzero exit. House taxonomy (recipe.yaml execute):
  1 invalid/transient · 2 not-configured · 3 decommissioned (create
  refuses; destroy still works) · 4 approval-required · 5 wall-refused
  (never retried around) · 6 UNDEAD (destroy unverified — escalate).
Usage errors exit 1, never argparse's default 2 (cst-qiru: an agent
following the protocol would misread a typo as 'run setup first').
"""

from __future__ import annotations

import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager, Undead
from .network import PermanentError, TransientError
from .state import Decommissioned, NoApiKey, NotConfigured


class _Parser(__import__("argparse").ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _fail(kind: str, message: str, code: int) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = _Parser(prog="gpod")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("list")

    sp = sub.add_parser("stock")
    sp.add_argument("--gpu-type", default=None)

    cp = sub.add_parser("create")
    cp.add_argument("--gpu-type", required=True)
    cp.add_argument("--name", required=True)
    cp.add_argument("--image", default=None,
                    help="-devel family, or a ratified serving image")
    cp.add_argument("--port", action="append", default=None, dest="ports",
                    help="serving images only: port to expose, e.g. "
                         "8000/http (repeatable; immutable after create)")
    cp.add_argument("--cmd-arg", action="append", default=None,
                    dest="cmd_args",
                    help="serving images only: CMD argument (repeatable)")
    cp.add_argument("--no-volume", action="store_true",
                    help="do not attach the configured volume (serving "
                         "pods that pull from HF need no model cache)")

    dp = sub.add_parser("destroy")
    dp.add_argument("--id", required=True, dest="pod_id")

    sub.add_parser("destroy-all")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--gpu-types", required=True,
                    help="comma-separated gpuTypeIds")
    gp.add_argument("--max-hourly", required=True,
                    help="USD ceiling per pod-hour")
    gp.add_argument("--max-pods", type=int, required=True)
    gp.add_argument("--region", required=True, dest="region_pin")
    gp.add_argument("--volume", default=None, dest="volume_id",
                    help="network volume id to attach (attach-only)")
    gp.add_argument("--serving-image", action="append", default=None,
                    dest="serving_images",
                    help="exact image pin allowed to run as a serving "
                         "pod (repeatable; ratified like every wall)")
    kp = asub.add_parser("set-key")
    kp.add_argument("--key-file", required=True)
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
            if not out.get("configured"):
                print(json.dumps(out))
                sys.exit(2)
        elif args.cmd == "list":
            out = manager.list()
        elif args.cmd == "stock":
            out = manager.stock(args.gpu_type)
        elif args.cmd == "create":
            out = manager.create(args.gpu_type, args.name, args.image,
                                 ports=args.ports, cmd_args=args.cmd_args,
                                 attach_volume=not args.no_volume)
        elif args.cmd == "destroy":
            out = manager.destroy(args.pod_id)
        elif args.cmd == "destroy-all":
            out = manager.destroy_all()
        elif args.cmd == "admin" and args.op == "configure":
            from decimal import Decimal
            out = manager.configure(
                args.gpu_types.split(","), Decimal(args.max_hourly),
                args.max_pods, args.region_pin, args.volume_id,
                serving_images=args.serving_images)
        elif args.cmd == "admin" and args.op == "set-key":
            out = manager.set_key(args.key_file)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured",
              f"missing config ({e}); run gpod admin configure first", 2)
    except NoApiKey as e:
        _fail("not-configured",
              f"missing API key ({e}); run gpod admin set-key first", 2)
    except Decommissioned as e:
        _fail("decommissioned",
              f"decommissioned at {e}; create refuses "
              f"(destroy still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("wall-refused", str(e), 5)
    except Undead as e:
        _fail("undead", str(e), 6)
    except TransientError as e:
        _fail("provider-transient", f"{e} — safe to retry after a pause", 1)
    except PermanentError as e:
        _fail("provider-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="gpod-approve",
        description="HUMAN helper: grant a one-shot approval token "
                    "for an admin op")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(
        __import__("scutl_gpod.state", fromlist=["StateDir"]).StateDir(),
        args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching "
                              "admin op"}))
