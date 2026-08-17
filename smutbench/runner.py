"""CLI: run the generated suite against a named policy.

  python -m smutbench.runner [--policy reference] [--seeds 1,2,3] \
         [--manifest recipes/wallet-base-sepolia/recipe.yaml]

Prints the suite report as JSON. Exit 0 on green (all outcomes, no
safety hard fail), 3 on safety hard fail, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ir, policies, scenarios

POLICIES = {
    "reference": policies.reference_policy,
    "retry-new-id": policies.retry_new_id_policy,
    "gullible": policies.gullible_policy,
    "false-success-truster": policies.false_success_truster_policy,
}

DEFAULT_MANIFEST = (Path(__file__).resolve().parent.parent
                    / "recipes/wallet-base-sepolia/recipe.yaml")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="smutbench")
    p.add_argument("--policy", choices=sorted(POLICIES), default="reference")
    p.add_argument("--seeds", default="1,2,3")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = p.parse_args(argv)

    recipe = ir.load(args.manifest)
    seeds = [int(s) for s in args.seeds.split(",") if s]
    report = scenarios.run_suite(recipe, POLICIES[args.policy], seeds)
    report["policy"] = args.policy
    print(json.dumps(report, indent=2, default=str))
    if report["safety"] == "HARD FAIL":
        return 3
    return 0 if report["outcome_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
