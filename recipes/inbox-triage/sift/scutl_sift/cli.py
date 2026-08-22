"""CLI surface. Two entry points:

  sift          — the agent-facing typed tool (JSON in/out, one op per call)
  sift-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes extend the house taxonomy with 6:
  2 not-configured · 3 decommissioned · 4 approval-required ·
  5 limit-refused (the fetch cap; never retried around) ·
  6 already-triaged (idempotency working: skip and move on)

There is deliberately no send subcommand and no --to flag anywhere;
their absence is invariant #1 of the manifest.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager
from .mailbox import PermanentError, TransientError
from .state import (AlreadyTriaged, Decommissioned, NoCredential,
                    NotConfigured, StateDir)


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sift")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("fetch")

    rp = sub.add_parser("read")
    rp.add_argument("--id", required=True)

    tp = sub.add_parser("triage")
    tp.add_argument("--id", required=True)
    tp.add_argument("--category", required=True)
    tp.add_argument("--summary", required=True)

    dp = sub.add_parser("draft")
    dp.add_argument("--reply-to", required=True,
                    help="message id being answered; the recipient is that "
                         "message's sender and cannot be set")
    dp.add_argument("--body-file", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--categories", required=True,
                    help="comma-separated category ids ('other' is implicit)")
    gp.add_argument("--max-fetch", required=True, type=int,
                    help="ceiling on messages fetched per run")
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "fetch":
            out = manager.fetch()
        elif args.cmd == "read":
            out = manager.read(args.id)
        elif args.cmd == "triage":
            out = manager.triage(args.id, args.category, args.summary)
        elif args.cmd == "draft":
            out = manager.draft(args.reply_to, args.body_file)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(args.categories.split(","), args.max_fetch)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except NoCredential as e:
        _fail("not-configured", f"missing mailbox credential ({e}); the "
                                f"human places it (setup step 'credential')", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"decommissioned at {e}; fetch/read/triage/"
                                f"draft refuse (status still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except AlreadyTriaged as e:
        _fail("already-triaged", f"message '{e}' already has a verdict in "
                                 f"triage.log; skip it and move on", 6)
    except TransientError as e:
        _fail("mailbox-transient", f"{e} — check sift status before any retry", 1)
    except PermanentError as e:
        _fail("mailbox-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="sift-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching op"}))
