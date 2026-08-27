"""CLI surface. Two entry points:

  msigner          — the agent-facing typed tool (JSON in/out, one op per call)
  msigner-approve  — the HUMAN-facing approval helper; not for agent use.
                     Ratchet and sweep grants carry SCOPE (the number / the
                     destination), typed by the human at approval time.

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Nothing here ever prints key material.

Exit codes (manifest recover block): 2 not-setup, 3 tombstoned,
4 approval-required, 5 cap-exceeded, 6 transient, 7 ceremony-incomplete,
9 panicked. Permanent errors exit 8 — NOTE this diverges from the inner
signer CLI (which uses 7 for permanent) because the mainnet manifest
assigns 7 to the ceremony gate; callers of msigner follow THIS table.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from scutl_signer.approvals import ApprovalRequired as InnerApprovalRequired
from scutl_signer.core import CapExceeded
from scutl_signer.network import PermanentError, TransientError
from scutl_signer.state import Revoked

from . import approvals
from .approvals import ApprovalRequired
from .core import RATCHETABLE, Custodian
from .custody import CeremonyIncomplete, Panicked


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="msigner")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    sp = sub.add_parser("sign")
    sp.add_argument("message")

    pp = sub.add_parser("pay")
    pp.add_argument("--payment-id", required=True)
    pp.add_argument("--to", required=True)
    pp.add_argument("--amount", required=True, help="USDC decimal, e.g. 0.05")

    au = sub.add_parser("authorize")
    au.add_argument("--payment-id", required=True)
    au.add_argument("--to", required=True)
    au.add_argument("--amount", required=True)
    au.add_argument("--valid-secs", type=int, default=600)

    rs = sub.add_parser("record-settled")
    rs.add_argument("--payment-id", required=True)
    rs.add_argument("--to", required=True)
    rs.add_argument("--amount", required=True)
    rs.add_argument("--tx", default=None)

    pn = sub.add_parser("panic")
    pn.add_argument("--reason", default="unspecified")

    ap = sub.add_parser("admin")
    asub = ap.add_subparsers(dest="op", required=True)
    kg = asub.add_parser("keygen")
    kg.add_argument("--cap-per-tx", required=True)
    kg.add_argument("--cap-daily", required=True)
    kg.add_argument("--cap-lifetime", required=True)
    kg.add_argument("--ratchet-delay-hours", default="24")
    asub.add_parser("backup-verify")
    rr = asub.add_parser("restore-rehearsal")
    rr.add_argument("--backup-dir", required=True,
                    help="directory holding the OFFLINE copies of "
                         "keystore.json and kek")
    rt = asub.add_parser("ratchet")
    rt.add_argument("--cap", required=True, choices=list(RATCHETABLE))
    rt.add_argument("--to", required=True, dest="to_amount",
                    help="the new cap value the human approved")
    asub.add_parser("unpanic")
    sw = asub.add_parser("sweep")
    sw.add_argument("--to", required=True, dest="to_address")
    sw.add_argument("--remainder", action="store_true")
    asub.add_parser("revoke")

    args = p.parse_args(argv)
    try:
        cust = Custodian()
    except PermanentError as e:
        _fail("permanent", str(e), 8)

    try:
        if args.cmd == "status":
            out = cust.status()
        elif args.cmd == "sign":
            out = cust.sign_message(args.message)
        elif args.cmd == "pay":
            out = cust.pay(args.payment_id, args.to, Decimal(args.amount))
        elif args.cmd == "authorize":
            out = cust.authorize(args.payment_id, args.to,
                                 Decimal(args.amount), args.valid_secs)
        elif args.cmd == "record-settled":
            out = cust.record_settled(args.payment_id, args.to,
                                      Decimal(args.amount), args.tx)
        elif args.cmd == "panic":
            out = cust.panic(args.reason)
        elif args.cmd == "admin" and args.op == "keygen":
            out = cust.keygen(Decimal(args.cap_per_tx),
                              Decimal(args.cap_daily),
                              Decimal(args.cap_lifetime),
                              Decimal(args.ratchet_delay_hours))
        elif args.cmd == "admin" and args.op == "backup-verify":
            out = cust.backup_verify()
        elif args.cmd == "admin" and args.op == "restore-rehearsal":
            out = cust.restore_rehearsal(args.backup_dir)
        elif args.cmd == "admin" and args.op == "ratchet":
            out = cust.ratchet(args.cap, Decimal(args.to_amount))
        elif args.cmd == "admin" and args.op == "unpanic":
            out = cust.unpanic()
        elif args.cmd == "admin" and args.op == "sweep":
            out = cust.sweep(args.to_address, remainder=args.remainder)
        else:  # admin revoke
            out = cust.revoke()
    except Panicked as e:
        _fail("panicked", str(e), 9)
    except Revoked as e:
        _fail("revoked", f"wallet revoked (tombstone for {e}); all ops refuse", 3)
    except (ApprovalRequired, InnerApprovalRequired) as e:
        _fail("approval-required", str(e), 4)
    except CapExceeded as e:
        _fail("cap-exceeded", str(e), 5)
    except CeremonyIncomplete as e:
        _fail("ceremony-incomplete", str(e), 7)
    except TransientError as e:
        _fail("transient", f"{e} — safe to retry with the SAME payment id", 6)
    except PermanentError as e:
        _fail("permanent", str(e), 8)
    except FileNotFoundError as e:
        _fail("not-setup", f"missing state ({e}); run setup first", 2)
    except ValueError as e:
        _fail("invalid", str(e), 1)

    print(json.dumps(out))


def approve(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="msigner-approve",
        description="HUMAN USE ONLY: grant one gated-op approval token. "
                    "Ratchet and sweep tokens are SCOPED — you type the "
                    "number / destination here, and the agent's op must "
                    "match it exactly.",
    )
    sub = p.add_subparsers(dest="op", required=True)
    for op in approvals.INNER_OPS + approvals.PLAIN_OPS:
        sub.add_parser(op)
    rt = sub.add_parser("ratchet")
    rt.add_argument("--cap", required=True, choices=list(RATCHETABLE))
    rt.add_argument("--to", required=True, dest="to_amount")
    sw = sub.add_parser("sweep")
    sw.add_argument("--to", required=True, dest="to_address")
    sw.add_argument("--remainder", action="store_true")

    args = p.parse_args(argv)
    wstate = Custodian().wstate
    if args.op == "ratchet":
        token = approvals.grant_ratchet(wstate, args.cap, args.to_amount)
        scope = f"{args.cap} -> {args.to_amount}"
    elif args.op == "sweep":
        token = approvals.grant_sweep(wstate, args.to_address, args.remainder)
        scope = (f"{'remainder' if args.remainder else 'micro'} -> "
                 f"{args.to_address}")
    else:
        token = approvals.grant(wstate, args.op)
        scope = "unscoped"
    print(json.dumps({"granted": args.op, "scope": scope, "token": token,
                      "consumed_by": "next matching msigner op"}))


if __name__ == "__main__":
    main()
