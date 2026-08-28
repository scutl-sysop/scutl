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

from . import discriminant, heldout, ir, policies, scenarios, subject
from .capp import heldout as cp_heldout
from .capp import policies as cp_policies
from .capp import scenarios as cp_scenarios
from .capp import subject as cp_subject
from .prov import heldout as pv_heldout
from .prov import policies as pv_policies
from .prov import scenarios as pv_scenarios
from .prov import subject as pv_subject
from .pserv import heldout as ps_heldout
from .pserv import policies as ps_policies
from .pserv import scenarios as ps_scenarios
from .pserv import subject as ps_subject
from .sift import heldout as sf_heldout
from .sift import policies as sf_policies
from .sift import scenarios as sf_scenarios
from .sift import subject as sf_subject
from .herald import heldout as hd_heldout
from .herald import policies as hd_policies
from .herald import scenarios as hd_scenarios
from .herald import subject as hd_subject
from .pulse import heldout as pl_heldout
from .pulse import policies as pl_policies
from .pulse import scenarios as pl_scenarios
from .pulse import subject as pl_subject
from .renew import heldout as rn_heldout
from .renew import policies as rn_policies
from .renew import scenarios as rn_scenarios
from .renew import subject as rn_subject
from .pwatch import heldout as pw_heldout
from .pwatch import policies as pw_policies
from .pwatch import scenarios as pw_scenarios
from .pwatch import subject as pw_subject
from .refund import heldout as rf_heldout
from .refund import policies as rf_policies
from .refund import scenarios as rf_scenarios
from .refund import subject as rf_subject
from .mwallet import heldout as mw_heldout
from .mwallet import policies as mw_policies
from .mwallet import scenarios as mw_scenarios
from .mwallet import subject as mw_subject
from .idbr import heldout as id_heldout
from .idbr import policies as id_policies
from .idbr import scenarios as id_scenarios
from .idbr import subject as id_subject
from .x402v2 import heldout as x4_heldout
from .x402v2 import policies as x4_policies
from .x402v2 import scenarios as x4_scenarios
from .x402v2 import subject as x4_subject

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

CP_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/capability-purchase/recipe.yaml")

SF_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/inbox-triage/recipe.yaml")

HD_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/messenger-reachability/recipe.yaml")

PL_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/status-digest/recipe.yaml")

PW_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/price-watch-and-buy/recipe.yaml")

RN_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/subscription-steward/recipe.yaml")

RF_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/refund-clerk/recipe.yaml")

MW_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/wallet-mainnet/recipe.yaml")

X4_MANIFEST = (Path(__file__).resolve().parent.parent
               / "recipes/x402-v2-client/recipe.yaml")

IDBR_MANIFEST = (Path(__file__).resolve().parent.parent
                 / "recipes/identity-backup-restore/recipe.yaml")

IDBR_POLICIES = {
    "reference": id_policies.reference_idbr,
    "green-stamper": id_policies.green_stamper_policy,
    "refresh-paperer": id_policies.refresh_paperer_policy,
    "amnesia-shrugger": id_policies.amnesia_shrugger_policy,
    "mute-archivist": id_policies.mute_archivist_policy,
}

RN_POLICIES = {
    "reference": rn_policies.reference_renew,
    "gullible-renewer": rn_policies.gullible_renewer_policy,
    "hike-absorber": rn_policies.hike_absorber_policy,
    "silent-keeper": rn_policies.silent_keeper_policy,
    "misreporter": rn_policies.misreporter_policy,
}

MW_POLICIES = {
    "reference": mw_policies.reference_wallet,
    "redirect-obeyer": mw_policies.redirect_obeyer_policy,
    "gate-crasher": mw_policies.gate_crasher_policy,
    "silent-wallet": mw_policies.silent_wallet_policy,
    "misreporter": mw_policies.misreporter_policy,
}

RF_POLICIES = {
    "reference": rf_policies.reference_refund,
    "gullible-clerk": rf_policies.gullible_clerk_policy,
    "park-jumper": rf_policies.park_jumper_policy,
    "silent-clerk": rf_policies.silent_clerk_policy,
    "misreporter": rf_policies.misreporter_policy,
}

PW_POLICIES = {
    "reference": pw_policies.reference_pwatch,
    "gullible-watcher": pw_policies.gullible_watcher_policy,
    "deal-chaser": pw_policies.deal_chaser_policy,
    "silent-holder": pw_policies.silent_holder_policy,
    "misreporter": pw_policies.misreporter_policy,
}

PL_POLICIES = {
    "reference": pl_policies.reference_pulse,
    "greenwasher": pl_policies.greenwasher_policy,
    "stale-truster": pl_policies.stale_truster_policy,
    "mute": pl_policies.mute_pulse_policy,
}

HD_POLICIES = {
    "reference": hd_policies.reference_herald,
    "flooder": hd_policies.flooder_policy,
    "gullible": hd_policies.gullible_herald_policy,
    "mute": hd_policies.mute_herald_policy,
}

SF_POLICIES = {
    "reference": sf_policies.reference_sift,
    "gullible": sf_policies.gullible_sift_policy,
    "cap-drainer": sf_policies.cap_drainer_policy,
    "amnesiac": sf_policies.amnesiac_policy,
}

CP_POLICIES = {
    "reference": cp_policies.reference_capp,
    "blind-repurchaser": cp_policies.blind_repurchaser_policy,
    "quota-plower": cp_policies.quota_plower_policy,
    "paperer": cp_policies.paperer_policy,
    "gullible": cp_policies.gullible_capp_policy,
}

X4_POLICIES = {
    "reference": x4_policies.reference_x402,
    "schema-obeyer": x4_policies.schema_obeyer_policy,
    "chain-blind": x4_policies.chain_blind_policy,
    "misreporter": x4_policies.misreporter_policy,
    "silent-client": x4_policies.silent_client_policy,
}

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
    "capability": {"policies": CP_POLICIES,
                   "scenarios": cp_scenarios.generate,
                   "heldout": cp_heldout,
                   "tools": cp_subject.TOOLS_CAPP,
                   "prompt_builder": cp_subject.build_system_prompt},
    "triage": {"policies": SF_POLICIES,
               "scenarios": sf_scenarios.generate,
               "heldout": sf_heldout,
               "tools": sf_subject.TOOLS_SIFT,
               "prompt_builder": sf_subject.build_system_prompt},
    "herald": {"policies": HD_POLICIES,
               "scenarios": hd_scenarios.generate,
               "heldout": hd_heldout,
               "tools": hd_subject.TOOLS_HERALD,
               "prompt_builder": hd_subject.build_system_prompt},
    "pulse": {"policies": PL_POLICIES,
              "scenarios": pl_scenarios.generate,
              "heldout": pl_heldout,
              "tools": pl_subject.TOOLS_PULSE,
              "prompt_builder": pl_subject.build_system_prompt},
    "renew": {"policies": RN_POLICIES,
              "scenarios": rn_scenarios.generate,
              "heldout": rn_heldout,
              "tools": rn_subject.TOOLS_RENEW,
              "prompt_builder": rn_subject.build_system_prompt},
    "pwatch": {"policies": PW_POLICIES,
               "scenarios": pw_scenarios.generate,
               "heldout": pw_heldout,
               "tools": pw_subject.TOOLS_PWATCH,
               "prompt_builder": pw_subject.build_system_prompt},
    "refund": {"policies": RF_POLICIES,
               "scenarios": rf_scenarios.generate,
               "heldout": rf_heldout,
               "tools": rf_subject.TOOLS_REFUND,
               "prompt_builder": rf_subject.build_system_prompt},
    "mwallet": {"policies": MW_POLICIES,
                "scenarios": mw_scenarios.generate,
                "heldout": mw_heldout,
                "tools": mw_subject.TOOLS_MWALLET,
                "prompt_builder": mw_subject.build_system_prompt},
    "x402v2": {"policies": X4_POLICIES,
               "scenarios": x4_scenarios.generate,
               "heldout": x4_heldout,
               "tools": x4_subject.TOOLS_X402V2,
               "prompt_builder": x4_subject.build_system_prompt},
    "idbr": {"policies": IDBR_POLICIES,
             "scenarios": id_scenarios.generate,
             "heldout": id_heldout,
             "tools": id_subject.TOOLS_IDBR,
             "prompt_builder": id_subject.build_system_prompt},
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
    p.add_argument("--discriminant", action="store_true",
                   help="run the obedience/capability discriminant "
                        "triplets (cst-b4e) instead of the public menu; "
                        "adds a per-family verdict to the report. "
                        "Unlocks the disc-* validation policies")
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
    bench_policies = bench["policies"]
    if args.discriminant:
        if args.heldout:
            p.error("--discriminant and --heldout are separate menus")
        scenario_gen = discriminant.generate
        bench_policies = {**bench_policies,
                          **discriminant.VALIDATION_POLICIES.get(
                              recipe.recipe_id, {})}
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
        if args.policy not in bench_policies:
            p.error(f"policy '{args.policy}' unknown for recipe "
                    f"'{recipe.recipe_id}' "
                    f"(have: {sorted(bench_policies)})")
        policy = bench_policies[args.policy]
        policy_name = args.policy
    report = scenarios.run_suite(recipe, policy, seeds,
                                 scenario_gen=scenario_gen)
    report["policy"] = policy_name
    if args.heldout:
        report["heldout_round"] = args.heldout
    if args.discriminant:
        report["discriminant"] = discriminant.verdicts(report["results"])
    print(json.dumps(report, indent=2, default=str))
    if report["safety"] == "HARD FAIL":
        return 3
    if args.discriminant:
        # the discriminant's headline is the verdict, not the raw rate:
        # a scripted obedient policy is EXPECTED to fail the permitted
        # corner. Green means every family verdict is 'discriminating'.
        good = all(v["verdict"] == "discriminating"
                   for v in report["discriminant"].values())
        return 0 if good else 1
    return 0 if report["outcome_rate"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
