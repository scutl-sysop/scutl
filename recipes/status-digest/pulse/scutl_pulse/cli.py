"""CLI surface. Three entry points:

  pulse            — the agent-facing typed tool (JSON in/out, one op per call)
  pulse-approve    — the HUMAN-facing approval helper; not for agent use
  pulse-clear-flag — the HUMAN-facing flag clearer; deliberately NOT an
                     op of the agent tool — no agent-reachable op
                     clears an anomaly flag

All results are single JSON objects on stdout; errors are JSON on
stderr with a nonzero exit. Exit codes follow the house taxonomy:
  2 not-configured · 3 decommissioned · 4 approval-required ·
  5 limit-refused (probe-round cap, or a digest with no fresh
  evidence; never retried around) · 6 duplicate-period (the digest
  already went; never composed again)

There is deliberately no way to pass a table row, a money figure, a
gap line, or a flag state into `pulse digest`: the computed fields
are invariant #1 of the manifest, and their absence from the argument
surface is the mechanism.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .checks import PermanentError, TransientError
from .core import LimitRefused, Manager
from .state import (Decommissioned, DuplicatePeriod, NotConfigured,

                    StateDir)


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
    p = _Parser(prog="pulse")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("probe")

    dp = sub.add_parser("digest")
    dp.add_argument("--period", required=True,
                    help="the CURRENT period key (pulse status names it); "
                         "past periods refuse — there is no backfill")
    dp.add_argument("--notes-file", required=True,
                    help="your narrative, carried verbatim in a separate "
                         "field; it cannot alter any computed field")

    rp = sub.add_parser("read")
    rp.add_argument("--id", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--period-hours", required=True, type=int,
                    help="digest period; one digest owed per period")
    gp.add_argument("--freshness-min", required=True, type=int,
                    help="max evidence age that renders as current")
    gp.add_argument("--max-probe-rounds", required=True, type=int,
                    help="ceiling on probe rounds per period")
    gp.add_argument("--checks-file", required=True,
                    help="JSON list of {id, kind, target} check specs")
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "probe":
            out = manager.probe()
        elif args.cmd == "digest":
            out = manager.digest(args.period, args.notes_file)
        elif args.cmd == "read":
            out = manager.read(args.id)
        elif args.cmd == "admin" and args.op == "configure":
            from pathlib import Path
            checks = json.loads(Path(args.checks_file).read_text())
            out = manager.configure(args.period_hours, args.freshness_min,
                                    args.max_probe_rounds, checks)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"decommissioned at {e}; probe/digest "
                                f"refuse (status still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except DuplicatePeriod as e:
        _fail("duplicate-period", f"period '{e}' already has its digest in "
                                  f"pulse.log; the heartbeat went — never "
                                  f"compose it again", 6)
    except TransientError as e:
        _fail("monitor-transient", f"{e} — run pulse status BEFORE any "
                                   f"retry; recorded evidence is in the "
                                   f"log", 1)
    except PermanentError as e:
        _fail("monitor-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="pulse-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching op"}))


def clear_flag_main(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="pulse-clear-flag",
        description="HUMAN helper: clear a latched anomaly flag. "
                    "Deliberately not an agent tool op.",
    )
    p.add_argument("check", help="check id whose flag to clear")
    args = p.parse_args(argv)
    try:
        out = Manager().clear_flag(args.check)
    except ValueError as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out))
