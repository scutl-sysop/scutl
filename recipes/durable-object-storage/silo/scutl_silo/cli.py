"""CLI surface. Two entry points:

  silo          — the agent-facing typed tool (JSON in/out, one op per call)
  silo-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/invalid · 2 not-configured/not-provisioned ·
  4 approval-required · 5 limit-refused (a code-enforced wall said no;
  never retried around) · 6 integrity (bytes or provider state failed
  verification — loud by design)

Every error path is scrubbed against the stored credentials before it
reaches a transcript: secrets appear in no output, no log line, no
error message (recipe.yaml invariant 9).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import (DenyListed, IntegrityError, LimitRefused, Manager,
                   NotProvisioned, UnknownKey, WallsUnratified)
from .state import NotConfigured, StateDir
from .store import StoreUnreachable

class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 ('invalid'), never argparse's default 2 —
    2 is the taxonomy's not-configured and an agent following the
    protocol would misread a typo as 'run setup first' (cst-qiru)."""
    def error(self, message):
        self.print_usage(__import__("sys").stderr)
        print(f"{self.prog}: error: {message}",
              file=__import__("sys").stderr)
        raise SystemExit(1)



def _scrub(text: str, state: StateDir) -> str:
    try:
        creds = state.load_creds()
    except Exception:
        creds = {}
    for v in creds.values():
        if v:
            text = text.replace(str(v), "<redacted>")
    return text


def main(argv: list[str] | None = None) -> None:
    p = _Parser(prog="silo")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("rehearse")
    sub.add_parser("inventory")

    pp = sub.add_parser("put")
    pp.add_argument("path")
    pp.add_argument("--set", dest="set_name", default="default")

    gp = sub.add_parser("get")
    gp.add_argument("key")
    gp.add_argument("--into", required=True)

    dp = sub.add_parser("delete")
    dp.add_argument("key")

    sub.add_parser("teardown")

    ap_ = sub.add_parser("admin")
    asub = ap_.add_subparsers(dest="op", required=True)
    cf = asub.add_parser("configure")
    cf.add_argument("--storage-cap-gb", type=int, default=20)
    cf.add_argument("--monthly-spend-cap-usd", type=int, default=10)
    cf.add_argument("--rehearsal-interval-days", type=int, default=7)
    cf.add_argument("--rehearsal-horizon-factor", type=int, default=2)
    cf.add_argument("--single-put-limit-mb", type=int, default=256)
    cf.add_argument("--bucket", default="silo")
    cf.add_argument("--region", default="us-east-1")
    cf.add_argument("--deny-glob", action="append", default=[],
                    help="additional deny globs (additive; builtins "
                         "cannot be subtracted)")
    pv = asub.add_parser("provision")
    pv.add_argument("--cluster-id", type=int, required=True)
    pv.add_argument("--tier-id", type=int, required=True)
    pv.add_argument("--label", default="scutl-silo")
    pv.add_argument("--key-file", required=True,
                    help="0600 file holding the object-storage-scoped "
                         "Vultr API key (NEVER the prov key)")

    args = p.parse_args(argv)
    state = StateDir()
    manager = Manager(state=state)

    def fail(kind: str, message: str, code: int = 1) -> None:
        print(json.dumps({"error": kind,
                          "message": _scrub(message, state)}),
              file=sys.stderr)
        sys.exit(code)

    try:
        if args.cmd == "status":
            out = manager.status()
        elif args.cmd == "report":
            out = manager.report()
        elif args.cmd == "rehearse":
            out = manager.rehearse()
        elif args.cmd == "inventory":
            out = manager.inventory()
        elif args.cmd == "put":
            out = manager.put(args.path, set_name=args.set_name)
        elif args.cmd == "get":
            out = manager.get(args.key, args.into)
        elif args.cmd == "delete":
            out = manager.delete(args.key)
        elif args.cmd == "teardown":
            from .s3live import VultrRail
            config = state.load_config()
            key_file = config.get("rail_key_file")
            if key_file:
                manager = Manager(state=state,
                                  rail=VultrRail(open(key_file).read().strip()))
            out = manager.teardown()
        elif args.cmd == "admin" and args.op == "configure":
            out = manager.configure(
                args.storage_cap_gb, args.monthly_spend_cap_usd,
                args.rehearsal_interval_days, args.rehearsal_horizon_factor,
                args.single_put_limit_mb, bucket=args.bucket,
                region=args.region, deny_globs=args.deny_glob)
        elif args.cmd == "admin" and args.op == "provision":
            from .s3live import VultrRail
            manager = Manager(state=state, rail=VultrRail(
                open(args.key_file).read().strip()))
            out = manager.provision(args.cluster_id, args.tier_id,
                                    args.label)
            # remember where the scoped key lives so teardown can reach
            # the rail without re-asking; the key itself stays in its file
            config = state.load_config()
            config["rail_key_file"] = args.key_file
            state.save_config(config)
        else:  # pragma: no cover
            fail("usage", "unknown command", 1)
    except NotConfigured as e:
        fail("not-configured",
             f"no config at {e}; run silo admin configure", 2)
    except (NotProvisioned, WallsUnratified) as e:
        fail("not-ready", str(e), 2)
    except ApprovalRequired as e:
        fail("approval-required", str(e), 4)
    except DenyListed as e:
        fail("deny-listed", str(e), 5)
    except LimitRefused as e:
        fail("limit-refused", str(e), 5)
    except UnknownKey as e:
        fail("unknown-key", f"no manifest entry for '{e}'", 5)
    except IntegrityError as e:
        fail("integrity", str(e), 6)
    except StoreUnreachable as e:
        fail("store-unreachable", str(e), 1)
    except (ValueError, OSError) as e:
        fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="silo-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
