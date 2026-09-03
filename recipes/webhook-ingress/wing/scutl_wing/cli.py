"""CLI surface. Two entry points:

  wing          — the agent-facing typed tool (JSON in/out, one op per call)
  wing-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/invalid · 2 not-configured · 4 approval-required ·
  5 limit-refused (a code-enforced wall said no; never retried around)

`wing serve` is the loopback receiver the daemon unit runs — it binds
127.0.0.1 only; Caddy (paid-service ingress component) is the sole
public listener.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals, receiver
from .approvals import ApprovalRequired
from .core import LimitRefused, Manager
from .schemes import BadDescriptor
from .state import NotConfigured, StateDir, UnknownSender

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
    p = _Parser(prog="wing")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("heartbeat")

    up = sub.add_parser("url")
    up.add_argument("sender_id")

    ep = sub.add_parser("events")
    ep.add_argument("--sender", default=None)
    ep.add_argument("--rejected", action="store_true")

    sp = sub.add_parser("sender")
    ssub = sp.add_subparsers(dest="op", required=True)
    ap_ = ssub.add_parser("add")
    ap_.add_argument("sender_id")
    ap_.add_argument("--descriptor", required=True,
                     help="JSON file: scheme descriptor, optionally with "
                          "a provider-issued 'secret' field")
    ap_.add_argument("--secret-out", default=None,
                     help="0600 file to write a minted secret to (required "
                          "when the descriptor carries none)")
    rp = ssub.add_parser("rotate")
    rp.add_argument("sender_id")
    rp.add_argument("--secret-out", required=True)

    vp = sub.add_parser("serve")
    vp.add_argument("--port", type=int, required=True)

    cp_ = sub.add_parser("admin")
    asub = cp_.add_subparsers(dest="op", required=True)
    cf = asub.add_parser("configure")
    cf.add_argument("--public-base-url", required=True)
    cf.add_argument("--replay-tolerance-seconds", type=int, default=300)
    cf.add_argument("--dedup-retention-days", type=int, default=90)
    cf.add_argument("--heartbeat-horizon-minutes", type=int, default=720)
    cf.add_argument("--reject-spike-threshold", type=int, default=10)
    cf.add_argument("--max-senders", type=int, default=4)
    cf.add_argument("--rotation-overlap-hours", type=int, default=24)

    args = p.parse_args(argv)
    manager = Manager()

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "report":
            out = manager.report()
        elif args.cmd == "heartbeat":
            out = manager.heartbeat()
        elif args.cmd == "url":
            out = manager.url(args.sender_id)
        elif args.cmd == "events":
            out = manager.events(sender=args.sender,
                                 rejected_only=args.rejected)
        elif args.cmd == "sender" and args.op == "add":
            spec = json.loads(open(args.descriptor).read())
            secret = spec.pop("secret", None)
            out = manager.sender_add(args.sender_id, spec, secret=secret,
                                     secret_out=args.secret_out)
        elif args.cmd == "sender" and args.op == "rotate":
            out = manager.sender_rotate(args.sender_id, args.secret_out)
        elif args.cmd == "serve":
            receiver.serve(manager.state, args.port)
            return  # pragma: no cover — serve_forever
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.public_base_url, args.replay_tolerance_seconds,
                args.dedup_retention_days, args.heartbeat_horizon_minutes,
                args.reject_spike_threshold, args.max_senders,
                args.rotation_overlap_hours)
        else:  # pragma: no cover
            _fail("usage", "unknown command", 1)
    except NotConfigured as e:
        _fail("not-configured",
              f"no config at {e}; run wing admin configure", 2)
    except UnknownSender as e:
        _fail("unknown-sender", f"no registered sender '{e}'", 5)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except LimitRefused as e:
        _fail("limit-refused", str(e), 5)
    except BadDescriptor as e:
        _fail("bad-descriptor", str(e), 1)
    except (ValueError, OSError) as e:
        _fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="wing-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
