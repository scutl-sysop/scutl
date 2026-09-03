"""CLI surface. Two entry points:

  beacon          — the agent-facing typed tool (JSON in/out, one op per call)
  beacon-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/invalid · 2 not-configured · 4 approval-required ·
  5 limit-refused (a code-enforced wall said no; never retried around)

`beacon probe <target>` is what bell-registered units invoke — the
local prover is the same code path whether a timer or a hand runs it.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager, WallsUnratified
from .rails import ProberUnreachable, RailError, TargetInvalid
from .state import NotConfigured, StateDir, UnknownTarget

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
    p = _Parser(prog="beacon")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("verify")

    rp = sub.add_parser("register")
    rp.add_argument("target_id")
    rp.add_argument("--url", required=True,
                    help="the health path customers-side probes hit")
    rp.add_argument("--sentinel", required=True,
                    help="the identity string only the real service "
                         "serves (min 8 chars; the content wall)")
    rp.add_argument("--cadence-seconds", type=int, required=True,
                    help="prober check cadence")
    rp.add_argument("--local-cadence-seconds", type=int, required=True,
                    help="bell schedule cadence of the local prover")

    pp = sub.add_parser("probe")
    pp.add_argument("target_id")
    pp.add_argument("--oid", default=None,
                    help="explicit observation id (retries reuse it; the "
                         "ledger refuses a duplicate — the window counts "
                         "it once)")

    dp = sub.add_parser("deregister")
    dp.add_argument("target_id")

    cp_ = sub.add_parser("admin")
    asub = cp_.add_subparsers(dest="op", required=True)
    cf = asub.add_parser("configure")
    cf.add_argument("--max-targets", type=int, default=40)
    cf.add_argument("--prober-horizon-factor", type=int, default=3)
    cf.add_argument("--prober-horizon-floor-minutes", type=int, default=20)
    cf.add_argument("--local-freshness-factor", type=int, default=2)
    cf.add_argument("--verifier-horizon-factor", type=int, default=2)
    cf.add_argument("--verify-cadence-seconds", type=int, default=900)
    cf.add_argument("--prober-api-base",
                    default="https://api.uptimerobot.com/v3")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "report":
            out = manager.report()
        elif args.cmd == "verify":
            out = manager.verify()
        elif args.cmd == "register":
            out = manager.register(args.target_id, args.url, args.sentinel,
                                   args.cadence_seconds,
                                   args.local_cadence_seconds)
        elif args.cmd == "probe":
            out = manager.probe(args.target_id, oid=args.oid)
        elif args.cmd == "deregister":
            out = manager.deregister(args.target_id)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.max_targets, args.prober_horizon_factor,
                args.prober_horizon_floor_minutes,
                args.local_freshness_factor,
                args.verifier_horizon_factor,
                args.verify_cadence_seconds,
                args.prober_api_base)
        else:  # pragma: no cover
            _fail("usage", "unknown command", 1)
    except NotConfigured as e:
        _fail("not-configured",
              f"no config at {e}; run beacon admin configure", 2)
    except WallsUnratified as e:
        _fail("walls-unratified", str(e), 2)
    except UnknownTarget as e:
        _fail("unknown-target", f"no registered target '{e}'", 5)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except TargetInvalid as e:
        _fail("target-invalid", str(e), 1)
    except ProberUnreachable as e:
        _fail("prober-unreachable", str(e), 1)
    except RailError as e:
        _fail("rail-error", str(e), 1)
    except (ValueError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="beacon-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
