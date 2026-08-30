"""CLI surface. Two entry points:

  bell          — the agent-facing typed tool (JSON in/out, one op per call)
  bell-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/invalid · 2 not-configured · 4 approval-required ·
  5 limit-refused (a code-enforced wall said no; never retried around)

`bell fire <job>` is what rendered service units invoke — the run
harness is the same code path whether a timer or a hand rings it.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager, WallsUnratified
from .rails import InvalidSchedule, RailError, WitnessUnreachable
from .state import NotConfigured, StateDir, UnknownJob


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="bell")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("verify")

    rp = sub.add_parser("register")
    rp.add_argument("job_id")
    rp.add_argument("--schedule", required=True,
                    help="systemd OnCalendar expression, UTC only")
    rp.add_argument("--argv", required=True,
                    help="JSON array: the command this job runs")

    vp = sub.add_parser("register-verifier")
    vp.add_argument("--schedule", required=True)

    fp = sub.add_parser("fire")
    fp.add_argument("job_id")
    fp.add_argument("--rid", default=None,
                    help="explicit run id (retries reuse it; the ledger "
                         "refuses a duplicate — the slot counts once)")

    dp = sub.add_parser("deregister")
    dp.add_argument("job_id")

    cp_ = sub.add_parser("admin")
    asub = cp_.add_subparsers(dest="op", required=True)
    cf = asub.add_parser("configure")
    cf.add_argument("--max-jobs", type=int, default=18)
    cf.add_argument("--grace-divisor", type=int, default=4)
    cf.add_argument("--grace-cap-minutes", type=int, default=60)
    cf.add_argument("--verifier-horizon-factor", type=int, default=2)
    cf.add_argument("--unwitnessed-streak-threshold", type=int, default=3)
    cf.add_argument("--witness-api-base",
                    default="https://healthchecks.io/api/v3")
    cf.add_argument("--witness-ping-base", default="https://hc-ping.com")

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
            out = manager.register(args.job_id, json.loads(args.argv),
                                   args.schedule)
        elif args.cmd == "register-verifier":
            out = manager.register_verifier(args.schedule)
        elif args.cmd == "fire":
            out = manager.fire(args.job_id, rid=args.rid)
        elif args.cmd == "deregister":
            out = manager.deregister(args.job_id)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.max_jobs, args.grace_divisor, args.grace_cap_minutes,
                args.verifier_horizon_factor,
                args.unwitnessed_streak_threshold,
                args.witness_api_base, args.witness_ping_base)
        else:  # pragma: no cover
            _fail("usage", "unknown command", 1)
    except NotConfigured as e:
        _fail("not-configured",
              f"no config at {e}; run bell admin configure", 2)
    except WallsUnratified as e:
        _fail("walls-unratified", str(e), 2)
    except UnknownJob as e:
        _fail("unknown-job", f"no registered job '{e}'", 5)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except InvalidSchedule as e:
        _fail("invalid-schedule", str(e), 1)
    except WitnessUnreachable as e:
        _fail("witness-unreachable", str(e), 1)
    except RailError as e:
        _fail("rail-error", str(e), 1)
    except (ValueError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="bell-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
