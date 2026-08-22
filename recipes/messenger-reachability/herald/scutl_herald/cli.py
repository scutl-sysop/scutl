"""CLI surface. Two entry points:

  herald          — the agent-facing typed tool (JSON in/out, one op per call)
  herald-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  2 not-configured · 3 decommissioned · 4 approval-required ·
  5 limit-refused (a send or fetch ceiling; never retried around) ·
  6 duplicate-key (the message already went; never re-sent)

There is deliberately no --to flag, no broadcast, and no recipient
input anywhere; the single-recipient confinement is invariant #1 of
the manifest, and its absence here is the mechanism.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .channel import PermanentError, TransientError
from .core import LimitRefused, Manager
from .state import (Decommissioned, DuplicateKey, NoCredential,
                    NotConfigured, StateDir)


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="herald")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("fetch")

    sp = sub.add_parser("send")
    sp.add_argument("--key", required=True,
                    help="caller-chosen idempotency key; a key already in "
                         "the log refuses (exit 6) — the message went")
    sp.add_argument("--body-file", required=True,
                    help="the message body; the recipient is the configured "
                         "owner and cannot be set")

    rp = sub.add_parser("read")
    rp.add_argument("--id", required=True)

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    gp = asub.add_parser("configure")
    gp.add_argument("--owner", required=True,
                    help="provider-verified peer id of the ONE human this "
                         "channel serves")
    gp.add_argument("--per-hour", required=True, type=int,
                    help="ceiling on sends per rolling hour")
    gp.add_argument("--per-day", required=True, type=int,
                    help="ceiling on sends per rolling day")
    gp.add_argument("--max-fetch", required=True, type=int,
                    help="ceiling on inbound messages fetched per run")
    asub.add_parser("decommission")

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "fetch":
            out = manager.fetch()
        elif args.cmd == "send":
            out = manager.send(args.key, args.body_file)
        elif args.cmd == "read":
            out = manager.read(args.id)
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(args.owner, args.per_hour,
                                    args.per_day, args.max_fetch)
        else:  # admin decommission
            out = manager.decommission()
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure first", 2)
    except NoCredential as e:
        _fail("not-configured", f"missing channel credential ({e}); the "
                                f"human places it (setup step 'credential')", 2)
    except Decommissioned as e:
        _fail("decommissioned", f"decommissioned at {e}; send/fetch/read "
                                f"refuse (status still works)", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except DuplicateKey as e:
        _fail("duplicate-key", f"send key '{e}' is already in herald.log; "
                               f"the message went — never re-send it under "
                               f"a new key", 6)
    except TransientError as e:
        _fail("channel-transient", f"{e} — a send that timed out may still "
                                   f"have delivered; run herald status "
                                   f"BEFORE any retry", 1)
    except PermanentError as e:
        _fail("channel-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="herald-approve",
        description="HUMAN helper: grant a one-shot approval token for a gated op",
    )
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "note": "consumed (deleted) by the next matching op"}))
