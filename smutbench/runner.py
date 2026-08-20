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

from . import heldout, ir, policies, scenarios, subject

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
    p.add_argument("--subject-url", metavar="BASE_URL",
                   help="grade a live model instead of a scripted policy: "
                        "OpenAI-compatible endpoint (llama.cpp llama-server "
                        "--jinja); overrides --policy")
    p.add_argument("--subject-model", default="default",
                   help="model name passed to the endpoint")
    p.add_argument("--subject-seed", type=int, default=None,
                   help="sampling seed passed to the endpoint")
    p.add_argument("--subject-max-steps", type=int, default=40)
    p.add_argument("--heldout", nargs="?", const=heldout.ACTIVE_ROUND,
                   metavar="ROUND",
                   help="grade against the held-out qualification set "
                        "instead of the public menu (default round: "
                        f"{heldout.ACTIVE_ROUND})")
    args = p.parse_args(argv)

    recipe = ir.load(args.manifest)
    seeds = [int(s) for s in args.seeds.split(",") if s]
    scenario_gen = None
    if args.heldout:
        scenario_gen = lambda rec: heldout.generate(rec, args.heldout)
    if args.subject_url:
        policy = subject.ModelSubject(
            recipe, subject.http_transport(args.subject_url),
            model=args.subject_model, seed=args.subject_seed,
            max_steps=args.subject_max_steps)
        policy_name = f"subject:{args.subject_model}@{args.subject_url}"
    else:
        policy = POLICIES[args.policy]
        policy_name = args.policy
    report = scenarios.run_suite(recipe, policy, seeds, scenario_gen)
    report["policy"] = policy_name
    if args.heldout:
        report["heldout_round"] = args.heldout
    print(json.dumps(report, indent=2, default=str))
    if report["safety"] == "HARD FAIL":
        return 3
    return 0 if report["outcome_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
