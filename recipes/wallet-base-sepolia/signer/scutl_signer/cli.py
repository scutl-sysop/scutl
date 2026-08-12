"""CLI surface. Two entry points:

  signer          — the agent-facing typed tool (JSON in/out, one op per call)
  signer-approve  — the HUMAN-facing approval helper; not for agent use
                    (manifest: wallet_admin ops gate on out-of-band approval)

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Nothing here ever prints key material.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from . import approvals
from .approvals import ApprovalRequired
from .core import CapExceeded, Signer
from .network import PermanentError, TransientError
from .state import Revoked, StateDir


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="signer")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    sp = sub.add_parser("sign")
    sp.add_argument("message")

    pp = sub.add_parser("pay")
    pp.add_argument("--payment-id", required=True)
    pp.add_argument("--to", required=True)
    pp.add_argument("--amount", required=True, help="USDC decimal, e.g. 0.05")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    kg = asub.add_parser("keygen")
    kg.add_argument("--cap-per-tx", required=True)
    kg.add_argument("--cap-daily", required=True)
    asub.add_parser("backup-verify")
    asub.add_parser("revoke")

    args = p.parse_args(argv)
    signer = Signer()

    try:
        if args.cmd == "status":
            out = signer.status()
        elif args.cmd == "sign":
            out = signer.sign_message(args.message)
        elif args.cmd == "pay":
            out = signer.pay(args.payment_id, args.to, Decimal(args.amount))
        elif args.cmd == "admin" and args.op == "keygen":
            out = signer.keygen(Decimal(args.cap_per_tx), Decimal(args.cap_daily))
        elif args.cmd == "admin" and args.op == "backup-verify":
            out = signer.backup_verify()
        else:  # admin revoke
            out = signer.revoke()
    except Revoked as e:
        _fail("revoked", f"wallet revoked (tombstone for {e}); all ops refuse", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except CapExceeded as e:
        _fail("cap-exceeded", str(e), 5)
    except TransientError as e:
        _fail("transient", f"{e} — safe to retry with the SAME payment id", 6)
    except PermanentError as e:
        _fail("permanent", str(e), 7)
    except FileNotFoundError as e:
        _fail("not-setup", f"missing state ({e}); run setup first", 2)

    print(json.dumps(out))


def approve(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="signer-approve",
        description="HUMAN USE ONLY: grant one admin-op approval token.",
    )
    p.add_argument("op", choices=list(approvals.ADMIN_OPS))
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token, "consumed_by": "next admin op"}))


if __name__ == "__main__":
    main()
