"""scutl lowering: emit a skill bundle from a recipe manifest.

    .venv/bin/python tools/emit.py recipes/wallet-base-sepolia \
        [--profile standard|smol|all] [--out build] \
        [--answer decide_id=option_id ...] [--param name=value ...]

The manifest is the source of truth; nothing recipe-specific lives here.
Two profiles (design: cst-8ih.1 target ladder):

  standard — frontier + reference rungs. Phased skill with rationale,
             fallbacks, and the failure-mode menu; assumes the model can
             branch and diagnose.
  smol     — headline rung. One resolved path, exact tool calls, one
             action at a time, state outside context: every step is a
             single command with an expected result and a stop rule.

Each bundle is SKILL.md + bundle.json (identity + config + manifest
sha256, consumed by the receipt record, cst-8ih.3).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

PROFILES = ("standard", "smol")


class LoweringError(Exception):
    pass


# --------------------------------------------------------------------------
# Resolution: Decide answers + parameters -> one blessed configuration
# --------------------------------------------------------------------------

def resolve(manifest: dict, answers: dict[str, str],
            params: dict[str, str]) -> dict:
    chosen: dict[str, dict] = {}
    for q in manifest["decide"]:
        opts = q["options"]
        if q["id"] in answers:
            match = [o for o in opts if o["id"] == answers[q["id"]]]
            if not match:
                raise LoweringError(
                    f"decide {q['id']}: no blessed option {answers[q['id']]!r} "
                    f"(blessed: {[o['id'] for o in opts]})")
            chosen[q["id"]] = match[0]
        elif len(opts) == 1:
            chosen[q["id"]] = opts[0]
        else:
            raise LoweringError(
                f"decide {q['id']} has {len(opts)} blessed options; "
                f"pass --answer {q['id']}=<option>")

    declared = manifest.get("parameters", {})
    # A parameter belongs to this configuration if a chosen option asks for
    # it, or if no option anywhere asks for it (recipe-global, e.g. a payTo
    # address every leaf needs). Parameters asked only by unchosen options
    # stay out.
    asked_by_any: set[str] = set()
    for q in manifest["decide"]:
        for o in q["options"]:
            for pid in o.get("asks", []):
                if pid not in declared:
                    raise LoweringError(
                        f"option asks undeclared parameter {pid!r}")
                asked_by_any.add(pid)
    wanted = [pid for pid in declared if pid not in asked_by_any]
    for opt in chosen.values():
        wanted += [p for p in opt.get("asks", []) if p not in wanted]

    values: dict[str, str] = {}
    for pid in wanted:
        if pid in params:
            values[pid] = params[pid]
        elif "default" in declared[pid]:
            values[pid] = declared[pid]["default"]
        else:
            raise LoweringError(
                f"parameter {pid!r} has no default; pass --param {pid}=<value>")
    for pid in params:
        if pid not in values:
            raise LoweringError(f"--param {pid} not asked by any chosen option")

    choices = {k: v["id"] for k, v in chosen.items()}
    return {"choices": choices,
            "labels": {k: v["label"] for k, v in chosen.items()},
            "parameters": values,
            # Lowering-time template slots: parameter values plus decide
            # choices, so commands may reference e.g. {offering}.
            "slots": {**choices, **values}}


_OPTIONAL = re.compile(r"\[([^][]*)\]")


def fill(template: str, values: dict[str, str]) -> str:
    # `[...]` marks an optional segment: kept (brackets stripped) when its
    # slots resolved, dropped whole when one didn't — so a command can carry
    # an argument only some Decide leaves ask for, e.g.
    # `configure ...[ --resource-path {resource_path}]`.
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return _OPTIONAL.sub(
        lambda m: "" if "{" in m.group(1) else m.group(1), out)


# --------------------------------------------------------------------------
# Rendering helpers (shared)
# --------------------------------------------------------------------------

def _tool_rows(manifest: dict, values: dict[str, str]) -> list[tuple[str, str]]:
    # Lowering-time slots (parameter values) are filled here; call-time
    # slots like {url} legitimately remain for the agent to fill.
    rows = []
    for comp in manifest["components"].values():
        for t in comp["tools"]:
            if "ops" in t:
                for op, cmd in t["ops"].items():
                    rows.append((f"{t['name']} {op}", fill(cmd, values)))
            else:
                rows.append((t["name"], fill(t.get("command", "(no command)"), values)))
    return rows


def _setup_lines(manifest: dict, values: dict[str, str], smol: bool) -> list[str]:
    lines = []
    for i, step in enumerate(manifest["setup"], 1):
        who = ("HUMAN" if step.get("actor") == "human"
               else "agent, human approval" if step.get("approval") == "human"
               else "agent")
        lines.append(f"### Step {i}: {step['step']}  ({who})")
        lines.append("")
        if step.get("actor") == "human":
            lines.append(f"Ask the human to do this, then wait: "
                         f"{' '.join(step['instructions'].split())}")
        elif not smol:
            lines.append(" ".join(step["run"].split()))
        for cmd in step.get("commands", []):
            lines.append("```\n" + fill(cmd, values) + "\n```")
        if step.get("expect"):
            lines.append(f"Expected: {step['expect']}")
        if step.get("verify"):
            lines.append(f"Verify: {step['verify']}")
        fb = step.get("fallback")
        if fb:
            if smol:
                lines.append(
                    f"If this fails ({', '.join(fb['trigger'])}): STOP and tell "
                    f"the human: {' '.join(fb['instructions'].split())} "
                    f"Then: {fb['verify']}")
            else:
                lines.append(
                    f"Fallback on {', '.join(fb['trigger'])} "
                    f"(actor: {fb.get('actor', 'agent')}): "
                    f"{' '.join(fb['instructions'].split())} "
                    f"Then: {fb['verify']}")
        lines.append("")
    return lines


_EXECUTE_LABELS = {"over_cap": "Over cap", "transient": "Transient error"}


def _execute_notes(ex: dict) -> list[tuple[str, str]]:
    # Every scalar annotation on execute renders as a bullet, in manifest
    # order; loop/command/guardrails have their own structure above.
    return [(_EXECUTE_LABELS.get(k, k.replace("_", " ").capitalize()),
             " ".join(str(v).split()))
            for k, v in ex.items()
            if k not in ("loop", "command", "guardrails")]


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def render_standard(manifest: dict, config: dict) -> str:
    r = manifest["recipe"]
    v = config["slots"]
    L: list[str] = []
    L += [f"# {r['title']} — skill bundle (standard profile)", ""]
    L += [f"Recipe `{r['id']}` rev {r['rev']} · status {r['status']} · "
          f"lowered from the scutl manifest — do not hand-edit; re-emit.", ""]
    L += [" ".join(r["summary"].split()), ""]

    L += ["## Configuration", ""]
    for qid, label in config["labels"].items():
        L.append(f"- **{qid}**: {label}")
    for pid, val in config["parameters"].items():
        L.append(f"- **{pid}**: {val}")
    L += [""]

    L += ["## Tools", "",
          "All safety lives in the signer component, in code — caps, key "
          "confinement, approval gates. You cannot lift them and must not "
          "try to work around them.", ""]
    for name, cmd in _tool_rows(manifest, v):
        L.append(f"- `{name}` — `{cmd}`")
    L += ["", "Component invariants (enforced in code; audit targets):", ""]
    for comp in manifest["components"].values():
        for inv in comp["invariants"]:
            L.append(f"- {' '.join(inv.split())}")
    L += [""]

    L += ["## Setup", ""]
    L += _setup_lines(manifest, v, smol=False)

    ex = manifest["execute"]
    L += ["## Execute (steady state)", "", " ".join(ex["loop"].split()), ""]
    if ex.get("command"):
        L += ["```", ex["command"], "```", ""]
    for key, note in _execute_notes(ex):
        L.append(f"- {key}: {note}")
    for g in ex.get("guardrails", []):
        L.append(f"- {' '.join(g.split())}")
    L += [""]

    L += ["## Verify (acceptance — the recipe is not installed until all pass)", ""]
    for chk in manifest["verify"]:
        L.append(f"- [ ] {' '.join(chk.split())}")
    L += [""]

    L += ["## Recover", ""]
    for name, proc in manifest["recover"].items():
        L.append(f"### {name}")
        L.append("")
        if proc.get("actor") == "human":
            L.append(f"Human performs: {' '.join(proc['instructions'].split())}")
        if proc.get("run"):
            gate = " (human approval required)" if proc.get("approval") == "human" else ""
            L.append(f"{' '.join(proc['run'].split())}{gate}")
        if proc.get("verify"):
            L.append(f"Verify: {proc['verify']}")
        L.append("")

    L += ["## Failure modes you may meet (from dependency contracts)", ""]
    for role, c in manifest["contracts"].items():
        L.append(f"- **{role}**: {', '.join(c['failure_modes'])}")
    L += [""]
    return "\n".join(L)


def render_smol(manifest: dict, config: dict) -> str:
    if "smol" in manifest:
        return _render_smol_from_manifest(manifest, config)
    return _render_smol_wallet_legacy(manifest, config)


def _render_smol_from_manifest(manifest: dict, config: dict) -> str:
    """Manifest-driven smol profile (recipes with a top-level `smol:` block).

    The recipe-specific single path — exit codes, the steady-state action,
    the emergency stop — comes from the manifest, so nothing recipe-shaped
    lives in this renderer. Block shape:

        smol:
          state_rule: <what never to remember; which command re-reads state>
          exit_codes: <one line, code -> action>
          action: { heading, intro, command?, notes: [..] }
          emergency: { trigger, command, after }
    """
    r = manifest["recipe"]
    v = config["slots"]
    ex = manifest["execute"]
    s = manifest["smol"]
    L: list[str] = []
    L += [f"# {r['title']} — skill bundle (smol profile)", ""]
    L += [f"Recipe `{r['id']}` rev {r['rev']} · lowered from the scutl "
          f"manifest — do not hand-edit; re-emit.", ""]
    rules = [
        "Do ONE step at a time. Run one command, read its output, "
        "then decide the single next step.",
        " ".join(s["state_rule"].split()),
        "Never print, ask for, or copy key material or approval tokens.",
        "If a command exits nonzero and no rule below covers it, STOP "
        "and show the human the exact JSON error.",
        "Exit codes: " + " ".join(s["exit_codes"].split()),
    ]
    L += ["## Rules — read first", ""]
    L += [f"{i}. {rule}" for i, rule in enumerate(rules, 1)]
    L += [""]
    L += ["## Setup — run once, in order", ""]
    L += _setup_lines(manifest, v, smol=True)
    act = s["action"]
    L += [f"## {act['heading']}", "",
          " ".join(act["intro"].split()), ""]
    if act.get("command", ex.get("command")):
        L += ["```", fill(act.get("command", ex.get("command")), v), "```", ""]
    for note in act.get("notes", []):
        L.append(f"- {' '.join(note.split())}")
    for key, note in _execute_notes(ex):
        L.append(f"- {key}: {note}")
    L += [""]
    em = s["emergency"]
    L += ["## Emergency stop", "",
          f"{' '.join(em['trigger'].split())} run exactly:", "",
          "```", fill(em["command"], v), "```", "",
          " ".join(em["after"].split()), ""]
    return "\n".join(L)


def _render_smol_wallet_legacy(manifest: dict, config: dict) -> str:
    # Wallet rev 1 predates the `smol:` manifest block, and its manifest is
    # frozen by pinned receipt hashes — this renderer keeps its bundles
    # byte-stable. New recipes declare `smol:` instead.
    r = manifest["recipe"]
    v = config["slots"]
    ex = manifest["execute"]
    L: list[str] = []
    L += [f"# {r['title']} — skill bundle (smol profile)", ""]
    L += [f"Recipe `{r['id']}` rev {r['rev']} · lowered from the scutl "
          f"manifest — do not hand-edit; re-emit.", ""]
    L += ["## Rules — read first", "",
          "1. Do ONE step at a time. Run one command, read its output, "
          "then decide the single next step.",
          "2. Never remember balances, caps, or spend totals. When you need "
          "wallet state, run `signer status` again.",
          "3. Never print, ask for, or copy key material or approval tokens.",
          "4. If a command exits nonzero and no rule below covers it, STOP "
          "and show the human the exact JSON error.",
          "5. Exit codes: 2 not-setup · 3 revoked (stop, tell human) · "
          "4 approval-required (ask human to approve, retry once) · "
          "5 cap-exceeded (stop, show offer to human) · "
          "6 transient (retry SAME payment id, max 3 tries) · 7 permanent (stop).",
          ""]
    L += ["## Setup — run once, in order", ""]
    L += _setup_lines(manifest, v, smol=True)
    L += ["## Paying for a resource", "",
          "When a request returns HTTP 402, run exactly:", "",
          "```", ex["command"], "```", "",
          "with `{url}` the resource URL and `{payment_id}` a new short id "
          "for this purchase (reuse the SAME id when retrying).",
          "",
          f"- Over cap: {ex['over_cap']}",
          f"- Transient: {ex.get('transient', 'retry with the same payment id.')}",
          ""]
    L += ["## Emergency stop", "",
          "If the human says stop/revoke/kill the wallet, run exactly:", "",
          "```", "signer admin revoke", "```", "",
          "It needs the human's approval token (exit 4 → ask them to run "
          "`signer-approve revoke`, retry once). Make no other decisions.", ""]
    return "\n".join(L)


RENDERERS = {"standard": render_standard, "smol": render_smol}


# --------------------------------------------------------------------------

def emit(recipe_dir: Path, out_root: Path, profile: str,
         answers: dict[str, str], params: dict[str, str]) -> Path:
    manifest_path = recipe_dir / "recipe.yaml"
    raw = manifest_path.read_bytes()
    manifest = yaml.safe_load(raw)
    config = resolve(manifest, answers, params)

    bundle_dir = out_root / manifest["recipe"]["id"] / profile
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "SKILL.md").write_text(RENDERERS[profile](manifest, config))
    (bundle_dir / "bundle.json").write_text(json.dumps({
        "recipe": manifest["recipe"]["id"],
        "rev": manifest["recipe"]["rev"],
        "profile": profile,
        "configuration": config["choices"],
        "parameters": config["parameters"],
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }, indent=2) + "\n")
    return bundle_dir


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("recipe_dir", type=Path)
    p.add_argument("--profile", default="all", choices=(*PROFILES, "all"))
    p.add_argument("--out", type=Path, default=Path("build"))
    p.add_argument("--answer", action="append", default=[],
                   metavar="DECIDE=OPTION")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    args = p.parse_args(argv)

    answers = dict(a.split("=", 1) for a in args.answer)
    params = dict(a.split("=", 1) for a in args.param)
    profiles = PROFILES if args.profile == "all" else (args.profile,)
    try:
        for prof in profiles:
            path = emit(args.recipe_dir, args.out, prof, answers, params)
            print(f"emitted {prof}: {path}")
    except LoweringError as e:
        sys.exit(f"lowering error: {e}")


if __name__ == "__main__":
    main()
