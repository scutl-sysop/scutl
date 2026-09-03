"""CLI surface. Two entry points:

  keep          — the agent-facing typed tool (JSON in/out, one op per call)
  keep-approve  — the HUMAN-facing approval helper; not for agent use

All results are single JSON objects on stdout; errors are JSON on stderr
with a nonzero exit. Exit codes follow the house taxonomy:
  1 transient/invalid/unreachable · 2 not-configured/not-provisioned ·
  4 approval-required · 5 limit-refused (a code-enforced wall said no;
  never retried around) · 6 integrity (ledgers, bytes, or provider
  state failed verification — loud by design)

Every error path is scrubbed against the custody credentials before it
reaches a transcript: the provider returns the admin password in a
plain GET, so scrubbing here is load-bearing, not cosmetic
(recipe.yaml invariant 9).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import approvals
from .approvals import ApprovalRequired
from .core import (IntegrityError, LimitRefused, Manager, NotProvisioned,
                   WallsUnratified)
from .state import NotConfigured, StateDir
from .wire import ClusterUnreachable

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
    for v in state.secret_values():
        text = text.replace(str(v), "<redacted>")
    return text


def _manager(state: StateDir) -> Manager:
    from .live import PgWire, SiloSeam, VultrDBRail
    try:
        config = state.load_config()
    except NotConfigured:
        config = {}
    rail = None
    key_file = config.get("rail_key_file")
    if key_file:
        rail = VultrDBRail(open(key_file).read().strip())
    db = PgWire(state, config) if config.get("cluster_id") else None
    return Manager(state=state, rail=rail, db=db, dumps=SiloSeam())


def main(argv: list[str] | None = None) -> None:
    p = _Parser(prog="keep")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("report")
    sub.add_parser("dump")
    sub.add_parser("rehearse")

    mp = sub.add_parser("migrate")
    mp.add_argument("--file", action="append", default=[],
                    help="explicitly offered migration file name(s); an "
                         "already-applied name refuses by ledger")

    pv = sub.add_parser("provision")
    pv.add_argument("--trusted-ip", action="append", required=True,
                    help="workshop egress address(es); the allowlist is "
                         "set BEFORE first use")
    pv.add_argument("--plan", default="vultr-dbaas-hobbyist-cc-1-25-1")
    pv.add_argument("--region", default="ewr")
    pv.add_argument("--key-file", required=True,
                    help="0600 file holding the Vultr API key (rev 1 "
                         "rides the prov key; the shared blast radius "
                         "is named in the manifest, not laundered)")

    sub.add_parser("teardown")

    ap_ = sub.add_parser("admin")
    asub = ap_.add_subparsers(dest="op", required=True)
    cf = asub.add_parser("configure")
    cf.add_argument("--monthly-spend-cap-usd", type=int, default=20)
    cf.add_argument("--dump-interval-days", type=int, default=1)
    cf.add_argument("--rehearsal-interval-days", type=int, default=7)
    cf.add_argument("--rehearsal-horizon-factor", type=int, default=2)
    cf.add_argument("--scratch-headroom-factor", type=int, default=3)
    cf.add_argument("--max-clusters", type=int, default=1)
    cf.add_argument("--migrations-dir")
    cf.add_argument("--database", action="append", default=None)
    cf.add_argument("--plan-rate-usd", type=float, default=15.0)

    args = p.parse_args(argv)
    state = StateDir()

    def fail(kind: str, message: str, code: int = 1) -> None:
        print(json.dumps({"error": kind,
                          "message": _scrub(message, state)}),
              file=sys.stderr)
        sys.exit(code)

    try:
        if args.cmd == "status":
            out = _manager(state).status()
        elif args.cmd == "report":
            out = _manager(state).full_report()
        elif args.cmd == "dump":
            out = _manager(state).dump()
        elif args.cmd == "rehearse":
            out = _manager(state).rehearse()
        elif args.cmd == "migrate":
            out = _manager(state).migrate(offered=args.file or None)
        elif args.cmd == "provision":
            from .live import PgWire, SiloSeam, VultrDBRail
            rail = VultrDBRail(open(args.key_file).read().strip())
            mgr = Manager(state=state, rail=rail, dumps=SiloSeam())
            out = mgr.provision(args.trusted_ip, plan=args.plan,
                                region=args.region)
            config = state.load_config()
            config["rail_key_file"] = args.key_file
            state.save_config(config)
        elif args.cmd == "teardown":
            out = _manager(state).teardown()
        elif args.cmd == "admin" and args.op == "configure":
            out = Manager(state=state).configure(
                args.monthly_spend_cap_usd, args.dump_interval_days,
                args.rehearsal_interval_days,
                args.rehearsal_horizon_factor,
                args.scratch_headroom_factor, args.max_clusters,
                migrations_dir=args.migrations_dir,
                databases=args.database,
                plan_rate_usd=args.plan_rate_usd)
        else:  # pragma: no cover
            fail("usage", "unknown command", 1)
    except NotConfigured as e:
        fail("not-configured",
             f"no config at {e}; run keep admin configure", 2)
    except (NotProvisioned, WallsUnratified) as e:
        fail("not-ready", str(e), 2)
    except ApprovalRequired as e:
        fail("approval-required", str(e), 4)
    except LimitRefused as e:
        fail("limit-refused", str(e), 5)
    except IntegrityError as e:
        fail("integrity", str(e), 6)
    except ClusterUnreachable as e:
        fail("cluster-unreachable", str(e), 1)
    except (ValueError, OSError) as e:
        fail("invalid", str(e), 1)
    print(json.dumps(out, indent=2))


def approve(argv: list[str] | None = None) -> None:
    p = _Parser(prog="keep-approve")
    p.add_argument("op", choices=approvals.ADMIN_OPS)
    args = p.parse_args(argv)
    token = approvals.grant(StateDir(), args.op)
    print(json.dumps({"granted": args.op, "token": token}))
