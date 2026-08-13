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


# ---------------------------------------------------------------------------
# Recipe #2 (paid-service): multi-leaf Decide, global parameters, smol block
# ---------------------------------------------------------------------------

PS_DIR = REPO / "recipes" / "paid-service-x402"
PS = yaml.safe_load((PS_DIR / "recipe.yaml").read_bytes())
PAYTO = "0x1111111111111111111111111111111111111111"


def test_multi_leaf_requires_answer_and_global_param():
    with pytest.raises(emit.LoweringError, match="2 blessed options"):
        emit.resolve(PS, {}, {"payto_address": PAYTO})
    # payto_address is asked by no option: recipe-global, no default -> required
    with pytest.raises(emit.LoweringError, match="payto_address"):
        emit.resolve(PS, {"offering": "generated-text",
                          "exposure": "lan-plaintext"}, {})


def test_global_params_and_choice_slots():
    cfg = emit.resolve(PS, {"offering": "generated-text",
                            "exposure": "lan-plaintext"},
                       {"payto_address": PAYTO})
    assert cfg["parameters"]["payto_address"] == PAYTO
    assert cfg["parameters"]["bind_port"] == "8402"      # global default
    assert "resource_path" not in cfg["parameters"]       # unchosen leaf's ask
    assert cfg["slots"]["offering"] == "generated-text"   # decide choice fills


def test_leaf_param_without_default_required():
    with pytest.raises(emit.LoweringError, match="resource_path"):
        emit.resolve(PS, {"offering": "static-file",
                          "exposure": "lan-plaintext"},
                     {"payto_address": PAYTO})


@pytest.fixture()
def ps_bundles(tmp_path):
    out = {}
    for offering in ("generated-text", "static-file"):
        params = {"payto_address": PAYTO}
        if offering == "static-file":
            params["resource_path"] = "/srv/paid/report.pdf"
        for prof in emit.PROFILES:
            d = emit.emit(PS_DIR, tmp_path / offering, prof,
                          {"offering": offering, "exposure": "lan-plaintext"},
                          params)
            out[(offering, prof)] = (d / "SKILL.md").read_text()
    return out


def test_ps_setup_fully_resolved_both_leaves(ps_bundles):
    for (offering, prof), skill in ps_bundles.items():
        setup = skill.split("## Keeping" if prof == "smol" else "## Execute")[0]
        for slot in ("{payto_address}", "{offering}", "{bind_addr}",
                     "{bind_port}", "{resource_path}", "["):
            assert slot not in setup, (offering, prof, slot)
        assert f"--offering {offering}" in setup


def test_ps_optional_segment_per_leaf(ps_bundles):
    assert "--resource-path /srv/paid/report.pdf" in ps_bundles[("static-file", "smol")]
    assert "--resource-path" not in ps_bundles[("generated-text", "smol")]


def test_ps_execute_annotations_render():
    cfg = emit.resolve(PS, {"offering": "generated-text",
                            "exposure": "lan-plaintext"},
                       {"payto_address": PAYTO})
    std = emit.render_standard(PS, cfg)
    assert "- Crashed: service_start once" in std


PS_TLS_ANSWERS = {"offering": "static-file", "exposure": "public-tls"}
PS_TLS_PARAMS = {"payto_address": PAYTO, "resource_path": "/srv/paid/report.pdf",
                 "public_hostname": "pay.scutl.example"}


def test_ps_when_gates_setup_and_verify_per_exposure_leaf(ps_bundles):
    # lan-plaintext (rev-1 behavior): no ingress steps, no public probes
    for prof in emit.PROFILES:
        skill = ps_bundles[("static-file", prof)]
        assert "install-proxy" not in skill
        assert "dns-record" not in skill
        assert "public_hostname" not in skill
    cfg = emit.resolve(PS, PS_TLS_ANSWERS, PS_TLS_PARAMS)
    std = emit.render_standard(PS, cfg)
    # public-tls: ingress steps present, resolved, human-actored
    assert "dns-record" in std and "install-proxy" in std
    assert "https://pay.scutl.example/resource" in std
    assert "{public_hostname}" not in std
    # bind_addr not asked on this leaf; optional segment must drop cleanly
    assert "--bind-addr" not in std
    assert "--bind-port 8402" in std
    # public-side verify checks render only here
    assert "containment: pserv's loopback port" in std
    assert "plaintext downgrade" in std
    lan = emit.render_standard(
        PS, emit.resolve(PS, {"offering": "generated-text",
                              "exposure": "lan-plaintext"},
                         {"payto_address": PAYTO}))
    assert "containment" not in lan


def test_when_unknown_decide_id_rejected():
    with pytest.raises(emit.LoweringError, match="unknown decide id"):
        emit.applies({"when": {"nonsense": "x"}}, {"exposure": "public-tls"})


def test_ps_smol_from_manifest(ps_bundles):
    smol = ps_bundles[("generated-text", "smol")]
    assert "ONE step at a time" in smol
    assert "pserv admin decommission" in smol            # emergency stop
    assert "signer" not in smol                          # nothing wallet-shaped
    assert "Failure modes" not in smol
    assert "Exit codes: 2 not-configured" in smol
