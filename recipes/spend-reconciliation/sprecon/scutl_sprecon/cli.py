"""sprecon CLI — thin argv mapping over Reconciler; config is paths and
an RPC URL only (the credential-free spine: no key, no kek, no API
key). The billing statement arrives as a file fetched by the recipe
that owns the provider key."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

from .chain import ChainAuditClient
from .core import Reconciler, ApprovalRequired, RESOLVE_OPS

class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 ('invalid'), never argparse's default 2 —
    2 is the taxonomy's not-configured and an agent following the
    protocol would misread a typo as 'run setup first' (cst-qiru)."""
    def error(self, message):
        self.print_usage(__import__("sys").stderr)
        print(f"{self.prog}: error: {message}",
              file=__import__("sys").stderr)
        raise SystemExit(1)



def _reconciler() -> Reconciler:
    wallet = os.environ.get("SPRECON_WALLET")
    if not wallet:
        sys.exit("SPRECON_WALLET (state dir of the audited wallet) is "
                 "required")
    chain = ChainAuditClient(
        rpc_url=os.environ.get("SPRECON_RPC", "https://sepolia.base.org"),
        usdc_address=os.environ.get(
            "SPRECON_USDC", "0x036CbD53842c5426634e7929541eC2318f3dCF7e"))
    return Reconciler(
        root=os.environ.get("SPRECON_ROOT",
                            os.path.expanduser("~/.scutl/sprecon")),
        chain=chain, wallet_dir=os.path.expanduser(wallet),
        pserv_dir=os.environ.get("SPRECON_PSERV"),
        prov_dir=os.environ.get("SPRECON_PROV"))


def _emit(doc) -> None:
    print(json.dumps(doc, indent=1, default=str))


def main(argv: list[str] | None = None) -> None:
    p = _Parser(prog="sprecon")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("status")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--billing-file")
    fnd = sub.add_parser("findings")
    fnd.add_argument("--state")
    res = sub.add_parser("resolve")
    res.add_argument("--finding", required=True)
    res.add_argument("--note", required=True)
    base = sub.add_parser("baseline")
    base.add_argument("--opening-balance", required=True)
    base.add_argument("--block", type=int, required=True)
    base.add_argument("--attestor", required=True)
    reb = sub.add_parser("rebaseline")
    reb.add_argument("--opening-balance", required=True)
    reb.add_argument("--block", type=int, required=True)
    reb.add_argument("--reason", required=True)
    args = p.parse_args(argv)

    r = _reconciler()
    try:
        if args.op == "status":
            _emit(r.status())
        elif args.op == "reconcile":
            billing = None
            if args.billing_file:
                billing = json.loads(open(args.billing_file).read())
            _emit(r.reconcile(billing=billing))
        elif args.op == "findings":
            _emit(r.findings(state=args.state))
        elif args.op == "resolve":
            _emit(r.resolve(args.finding, args.note))
        elif args.op == "baseline":
            _emit(r.baseline(Decimal(args.opening_balance), args.block,
                             args.attestor))
        elif args.op == "rebaseline":
            _emit(r.rebaseline(Decimal(args.opening_balance), args.block,
                               args.reason))
    except ApprovalRequired as e:
        sys.exit(str(e))


def approve(argv: list[str] | None = None) -> None:
    """Out-of-band human approval: sprecon-approve <op>."""
    p = _Parser(prog="sprecon-approve")
    p.add_argument("op", choices=RESOLVE_OPS)
    args = p.parse_args(argv)
    _reconciler().grant_approval(args.op)
    print(f"approval token created for '{args.op}' (consumed on next use)")
