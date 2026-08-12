"""Lowering tests: the emitter resolves one blessed configuration and the
emitted bundles keep the safety-relevant content from the manifest."""

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import emit  # noqa: E402

RECIPE_DIR = REPO / "recipes" / "wallet-base-sepolia"
MANIFEST = yaml.safe_load((RECIPE_DIR / "recipe.yaml").read_bytes())


@pytest.fixture()
def bundles(tmp_path):
    out = {}
    for prof in emit.PROFILES:
        d = emit.emit(RECIPE_DIR, tmp_path, prof, {}, {})
        out[prof] = {"skill": (d / "SKILL.md").read_text(),
                     "meta": json.loads((d / "bundle.json").read_text())}
    return out


def test_resolves_single_blessed_leaf():
    cfg = emit.resolve(MANIFEST, {}, {})
    assert cfg["choices"] == {"custody": "local-keyfile",
                              "attendance": "unattended-under-cap",
                              "network": "base-sepolia"}
    assert cfg["parameters"] == {"cap_per_tx": "0.10", "cap_daily": "1.00"}


def test_param_override_and_unknown_param():
    cfg = emit.resolve(MANIFEST, {}, {"cap_per_tx": "0.05"})
    assert cfg["parameters"]["cap_per_tx"] == "0.05"
    with pytest.raises(emit.LoweringError):
        emit.resolve(MANIFEST, {}, {"nope": "1"})


def test_bad_answer_rejected():
    with pytest.raises(emit.LoweringError):
        emit.resolve(MANIFEST, {"network": "base-mainnet"}, {})


def test_ambiguous_decide_requires_answer():
    m = {**MANIFEST, "decide": [{
        "id": "q", "question": "?",
        "options": [{"id": "a", "label": "A", "blessed": True},
                    {"id": "b", "label": "B", "blessed": True}]}]}
    with pytest.raises(emit.LoweringError, match="2 blessed options"):
        emit.resolve(m, {}, {})
    assert emit.resolve(m, {"q": "b"}, {})["choices"]["q"] == "b"


def test_setup_commands_have_no_unfilled_slots(bundles):
    # {url}/{payment_id} in Execute are agent-filled at call time; setup
    # commands must be fully resolved by the lowering.
    for prof, b in bundles.items():
        setup = b["skill"].split("## Paying" if prof == "smol" else "## Execute")[0]
        assert "{cap_per_tx}" not in setup and "{op}" not in setup, prof
        assert "signer admin keygen --cap-per-tx 0.10 --cap-daily 1.00" in setup


def test_invariants_and_exact_commands_survive_lowering(bundles):
    std = bundles["standard"]["skill"]
    for inv in MANIFEST["components"]["signer"]["invariants"]:
        assert " ".join(inv.split()) in std
    for b in bundles.values():
        assert MANIFEST["execute"]["command"] in b["skill"]


def test_smol_is_single_path(bundles):
    smol = bundles["smol"]["skill"]
    assert "ONE step at a time" in smol
    assert "signer admin revoke" in smol          # emergency stop present
    assert "Failure modes" not in smol            # no open-ended menu
    # human-performed steps must not read as agent instructions
    assert "Ask the human to do this, then wait" in smol


def test_bundle_metadata(bundles):
    for prof, b in bundles.items():
        assert b["meta"]["profile"] == prof
        assert b["meta"]["recipe"] == "wallet"
        assert len(b["meta"]["manifest_sha256"]) == 64
    assert (bundles["standard"]["meta"]["manifest_sha256"]
            == bundles["smol"]["meta"]["manifest_sha256"])
