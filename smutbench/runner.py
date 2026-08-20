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
from .prov import heldout as pv_heldout
from .prov import policies as pv_policies
from .prov import scenarios as pv_scenarios
from .prov import subject as pv_subject
from .pserv import heldout as ps_heldout
from .pserv import policies as ps_policies
from .pserv import scenarios as ps_scenarios
from .pserv import subject as ps_subject

POLICIES = {
    "reference": policies.reference_policy,
    "retry-new-id": policies.retry_new_id_policy,
    "gullible": policies.gullible_policy,
    "false-success-truster": policies.false_success_truster_policy,
}

PS_POLICIES = {
    "reference": ps_policies.reference_operator,
    "flapper": ps_policies.flapper_policy,
    "estimator": ps_policies.estimator_policy,
    "gullible": ps_policies.gullible_operator_policy,
}

DEFAULT_MANIFEST = (Path(__file__).resolve().parent.parent
                    / "recipes/wallet-base-sepolia/recipe.yaml")
PS_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/paid-service-x402/recipe.yaml")
PV_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/provision-vultr/recipe.yaml")

PV_POLICIES = {
    "reference": pv_policies.reference_provisioner,
    "double-creator": pv_policies.double_creator_policy,
    "limit-shopper": pv_policies.limit_shopper_policy,
    "orphaner": pv_policies.orphaner_policy,
    "gullible": pv_policies.gullible_prov_policy,
}

# recipe_id -> the modules that derive its bench. Adding a recipe here
# (plus its scenario/mock modules) is the WHOLE registration step.
BENCHES = {
    "wallet": {"policies": POLICIES, "scenarios": scenarios.generate,
               "heldout": heldout, "tools": None, "prompt_builder": None},
    "paid-service": {"policies": PS_POLICIES,
                     "scenarios": ps_scenarios.generate,
                     "heldout": ps_heldout,
                     "tools": ps_subject.TOOLS_PSERV,
                     "prompt_builder": ps_subject.build_system_prompt},
    "provision": {"policies": PV_POLICIES,
                  "scenarios": pv_scenarios.generate,
                  "heldout": pv_heldout,
                  "tools": pv_subject.TOOLS_PROV,
                  "prompt_builder": pv_subject.build_system_prompt},
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="smutbench")
    p.add_argument("--policy", default="reference",
                   help="scripted policy name (per-recipe: wallet has "
                        f"{sorted(POLICIES)}, paid-service has "
                        f"{sorted(PS_POLICIES)})")
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
    p.add_argument("--subject-think-budget", type=float,
                   default=subject.THINK_BUDGET, metavar="SECONDS",
                   help="wall-clock cap per generation; an overrun is a "
                        "scored failure, not an infra abort "
                        f"(default {subject.THINK_BUDGET:g}s)")
    p.add_argument("--heldout", nargs="?", const="ACTIVE", metavar="ROUND",
                   help="grade against the held-out qualification set "
                        "instead of the public menu (default: the "
                        "recipe's ACTIVE_ROUND)")
    args = p.parse_args(argv)

    recipe = ir.load(args.manifest)
    if recipe.recipe_id not in BENCHES:
        p.error(f"no bench registered for recipe '{recipe.recipe_id}' "
                f"(have: {sorted(BENCHES)})")
    bench = BENCHES[recipe.recipe_id]
    seeds = [int(s) for s in args.seeds.split(",") if s]
    scenario_gen = bench["scenarios"]
    if args.heldout:
        if args.heldout == "ACTIVE":
            args.heldout = bench["heldout"].ACTIVE_ROUND
        scenario_gen = lambda rec: bench["heldout"].generate(rec, args.heldout)
    if args.subject_url:
        policy = subject.ModelSubject(
            recipe,
            subject.http_transport(args.subject_url,
                                   think_budget=args.subject_think_budget),
            model=args.subject_model, seed=args.subject_seed,
            max_steps=args.subject_max_steps,
            tools=bench["tools"], prompt_builder=bench["prompt_builder"])
        policy_name = f"subject:{args.subject_model}@{args.subject_url}"
    else:
        if args.policy not in bench["policies"]:
            p.error(f"policy '{args.policy}' unknown for recipe "
                    f"'{recipe.recipe_id}' "
                    f"(have: {sorted(bench['policies'])})")
        policy = bench["policies"][args.policy]
        policy_name = args.policy
    report = scenarios.run_suite(recipe, policy, seeds,
                                 scenario_gen=scenario_gen)
    report["policy"] = policy_name
    if args.heldout:
        report["heldout_round"] = args.heldout
    print(json.dumps(report, indent=2, default=str))
    if report["safety"] == "HARD FAIL":
        return 3
    return 0 if report["outcome_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
