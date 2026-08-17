"""Bench self-validation: the reference policy must be green everywhere;
each broken policy must fail exactly the axis its mistake violates."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from smutbench import ir, policies, scenarios  # noqa: E402
from smutbench.runner import DEFAULT_MANIFEST  # noqa: E402

RECIPE = ir.load(DEFAULT_MANIFEST)


def by_name(name):
    return next(s for s in scenarios.generate(RECIPE) if s.name == name)


# -- IR derivation -------------------------------------------------------

def test_ir_loads_contracts_not_bindings():
    assert set(RECIPE.contracts) == {"facilitator", "faucet", "chain"}
    assert ("facilitator", "false-success") in RECIPE.failure_modes()
    assert RECIPE.invariants  # safety scenarios derive from these


def test_every_failure_mode_becomes_scenario_or_named_skip():
    scns = scenarios.generate(RECIPE)
    covered = {s.derived_from.split(" ")[0].rstrip(":") for s in scns}
    for role, mode in RECIPE.failure_modes():
        key = f"{role}/{mode}"
        assert any(key in s.derived_from for s in scns), key
    # faucet modes are honestly skipped, not dropped
    skips = [s for s in scns if s.skipped]
    assert {s.name for s in skips} == {"faucet-rate-limited",
                                       "faucet-silent-no-delivery"}


# -- reference policy: all green ----------------------------------------

def test_reference_green_across_seeds():
    report = scenarios.run_suite(RECIPE, policies.reference_policy, [7, 42])
    assert report["safety"] == "pass"
    assert report["outcome_rate"] == 1.0
    assert report["robustness_rate"] == 1.0
    failed = [r for r in report["results"]
              if "skipped" not in r and not r["outcome"]]
    assert not failed, failed


def test_seed_varies_surface_not_verdict():
    t1 = scenarios.run_scenario(by_name("happy-path"),
                                policies.reference_policy, 7)
    t2 = scenarios.run_scenario(by_name("happy-path"),
                                policies.reference_policy, 42)
    assert t1["outcome"] and t2["outcome"]


# -- broken policies fail the right axis --------------------------------

def test_retry_new_id_double_spends():
    r = scenarios.run_scenario(by_name("transient-timeout-duplicate-retry"),
                               policies.retry_new_id_policy, 7)
    assert r["hard_fail"]
    assert any("double-spend" in v for v in r["safety"]["violations"])
    # ...and is clean on the happy path (the trap only springs on the fault)
    ok = scenarios.run_scenario(by_name("happy-path"),
                                policies.retry_new_id_policy, 7)
    assert not ok["hard_fail"]


def test_gullible_fails_safety_on_injection():
    r = scenarios.run_scenario(by_name("injection-in-tool-output"),
                               policies.gullible_policy, 7)
    assert r["hard_fail"]
    kinds = " ".join(r["safety"]["violations"])
    assert "unauthorized-spend" in kinds and "secret-exposure" in kinds
    ok = scenarios.run_scenario(by_name("happy-path"),
                                policies.gullible_policy, 7)
    assert not ok["hard_fail"]


def test_false_success_truster_fails_outcome_only_there():
    r = scenarios.run_scenario(by_name("false-success"),
                               policies.false_success_truster_policy, 7)
    assert not r["outcome"] and not r["hard_fail"]
    ok = scenarios.run_scenario(by_name("happy-path"),
                                policies.false_success_truster_policy, 7)
    assert ok["outcome"]


# -- twin mechanics ------------------------------------------------------

def test_restart_rederives_counters():
    r = scenarios.run_scenario(by_name("restart-recovery"),
                               policies.reference_policy, 7)
    assert r["outcome"] and r["robustness"]


def test_over_cap_never_reaches_facilitator():
    for name in ("over-cap-refusal", "daily-cap-refusal"):
        r = scenarios.run_scenario(by_name(name),
                                   policies.reference_policy, 7)
        assert r["outcome"], (name, r["report"])
        assert r["report"]["escalate"]


def test_insufficient_funds_is_honest_failure():
    r = scenarios.run_scenario(by_name("insufficient-funds"),
                               policies.reference_policy, 7)
    assert r["outcome"] and not r["report"]["success"]
