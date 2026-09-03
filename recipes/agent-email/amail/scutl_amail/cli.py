"""CLI surface: amail — the agent-facing typed tool (JSON in/out).

All results are single JSON objects on stdout; errors are JSON on
stderr with a nonzero exit. Exit codes follow the house taxonomy:
  2 not-configured · 5 limit-refused (allowlist, ceiling, or
  inbound-trust policy; never retried around) · 6 duplicate-send-id
  (the send already went; never re-sent under a fresh id)

There is deliberately no allowlist-editing subcommand on the agent
surface: 'admin configure' rewrites the whole config and is an OWNER
operation per the manifest (send-authority decide node) — the agent
may ask the owner, and that is all it can do.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import LimitRefused, Manager
from .provider import PermanentError, TransientError
from .state import DuplicateSendId, NoCredential, NotConfigured

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
    p = _Parser(prog="amail")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    lp = sub.add_parser("list")
    lp.add_argument("--unreplied", action="store_true")

    rp = sub.add_parser("read")
    rp.add_argument("thread_id")

    sp = sub.add_parser("send")
    sp.add_argument("--send-id", required=True,
                    help="caller-chosen idempotency key; an id already in "
                         "the log refuses (exit 6) — the send went")
    sp.add_argument("--to", required=True,
                    help="comma-separated recipients; every one must be on "
                         "the send allowlist")
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body-file", required=True)

    yp = sub.add_parser("reply")
    yp.add_argument("--send-id", required=True)
    yp.add_argument("--thread", required=True,
                    help="thread to answer; the reply targets its tail "
                         "message and there is no recipient input")
    yp.add_argument("--body-file", required=True)

    gp = sub.add_parser("log")
    gp.add_argument("--reconcile", action="store_true",
                    help="compare the mail log against provider history; "
                         "disagreements are named findings, never absorbed")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    cp = asub.add_parser("configure")
    cp.add_argument("--inbox", required=True)
    cp.add_argument("--allow", action="append", default=[],
                    help="allowlist entry (address or domain); repeatable. "
                         "OWNER operation: ratifies the whole list")
    cp.add_argument("--daily-ceiling", required=True, type=int)
    cp.add_argument("--first-contact", required=True,
                    choices=["refuse", "draft-gate", "send"])

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "list":
            out = manager.list_unreplied()
        elif args.cmd == "read":
            out = manager.read(args.thread_id)
        elif args.cmd == "send":
            out = manager.send(args.send_id, args.to, args.subject,
                               args.body_file)
        elif args.cmd == "reply":
            out = manager.reply(args.send_id, args.thread, args.body_file)
        elif args.cmd == "log":
            out = manager.log(reconcile=args.reconcile)
        else:  # admin configure
            out = manager.configure(args.inbox, args.allow,
                                    args.daily_ceiling, args.first_contact)
    except NotConfigured as e:
        _fail("not-configured", f"missing config ({e}); run admin configure "
                                f"first (owner operation)", 2)
    except NoCredential as e:
        _fail("not-configured", f"missing provider credential ({e}); the "
                                f"human places it (setup: inbox-owned)", 2)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except DuplicateSendId as e:
        _fail("duplicate-send-id",
              f"send id '{e}' is already in amail.log; the send went (or "
              f"was in flight at a crash) — run amail log --reconcile, "
              f"never re-send under a fresh id", 6)
    except TransientError as e:
        _fail("provider-transient",
              f"{e} — a send that timed out may still have gone out; run "
              f"amail log --reconcile BEFORE any retry, then retry with "
              f"the SAME send id", 1)
    except PermanentError as e:
        _fail("provider-refused", str(e), 1)
    except (ValueError, RuntimeError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))
