"""CLI surface. Three entry points:

  idbackup          — the agent-facing typed tool (JSON in/out, one op per call)
  idbackup-approve  — HUMAN: grant the (one) gated-op token, 'rehearse'
  idbackup-attest   — HUMAN: record that an offline copy exists at a location

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Nothing here ever prints key material — digests
stand in for secrets everywhere.

Exit codes (manifest recover block): 2 not-setup, 3 tombstoned,
4 approval-required, 9 panicked, 10 unverified-backup, 1 invalid.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import (ApprovalRequired, Archivist, Panicked, Tombstoned,

                   UnverifiedBackup)


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
    p = _Parser(prog="idbackup")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    mf = sub.add_parser("manifest")
    mf.add_argument("--locations", type=int, default=2,
                    help="offline copies the human must attest")
    mf.add_argument("--staleness-days", type=int, default=30)

    vf = sub.add_parser("verify")
    vf.add_argument("--backup-dir", required=True,
                    help="directory holding one offline copy")

    rh = sub.add_parser("rehearse")
    rh.add_argument("--backup-dir", required=True)

    ow = sub.add_parser("own")
    ow.add_argument("--provider", required=True)
    ow.add_argument("--resource", required=True)
    ow.add_argument("--price", default=None)
    ow.add_argument("--evidence", default=None)

    args = p.parse_args(argv)
    arch = Archivist()
    try:
        if args.cmd == "status":
            out = arch.status()
        elif args.cmd == "manifest":
            out = arch.manifest(args.locations, args.staleness_days)
        elif args.cmd == "verify":
            out = arch.verify(args.backup_dir)
        elif args.cmd == "rehearse":
            out = arch.rehearse(args.backup_dir)
        else:  # own — the buying recipe's registry hook
            out = arch.record_owned_resource(args.provider, args.resource,
                                             args.price, args.evidence)
    except Panicked as e:
        _fail("panicked", str(e), 9)
    except Tombstoned as e:
        _fail("tombstoned",
              f"identity revoked (tombstone for {e}); the tombstone is the "
              f"report", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except UnverifiedBackup as e:
        _fail("unverified-backup", str(e), 10)
    except FileNotFoundError as e:
        _fail("not-setup", str(e), 2)
    except ValueError as e:
        _fail("invalid", str(e), 1)

    print(json.dumps(out))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="idbackup-approve",
        description="HUMAN USE ONLY: grant the rehearse-op approval token.")
    p.add_argument("op", choices=["rehearse"])
    args = p.parse_args(argv)
    token = Archivist().grant(args.op)
    print(json.dumps({"granted": args.op, "token": token,
                      "consumed_by": "next idbackup rehearse"}))


def attest(argv: list[str] | None = None) -> None:
    p = _Parser(
        prog="idbackup-attest",
        description="HUMAN USE ONLY: record that an offline copy matching "
                    "the current manifest exists at a location.")
    p.add_argument("--location", required=True,
                   help="a label you will recognize later, e.g. 'safe-A'")
    args = p.parse_args(argv)
    try:
        out = Archivist().attest(args.location)
    except FileNotFoundError as e:
        _fail("not-setup", str(e), 2)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
