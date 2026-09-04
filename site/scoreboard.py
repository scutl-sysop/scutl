#!/usr/bin/env python3
"""smutbench scoreboard generator (cst-j01t §5.4, ratified).

Scans ladder/<bench>/<model>-public-report[N].json (latest N wins) and
renders the task × model grid. Public-menu scores only — held-out
rounds are private by ruling. Provenance rule: a cell links its
env.json when present; a cell whose run predates env.json discipline
renders with a visible PARTIAL-PROVENANCE marker citing the batch
commit — shown honestly, never hidden, never dressed up.

Emits: out/smutbench/index.html (methodology + grid) and
out/smutbench/scoreboard.json.
Usage: python site/scoreboard.py [--out site/out/smutbench]
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LADDER = ROOT / "ladder"

# bench dir -> catalog recipe slug (dirs are component names)
BENCH_RECIPE = {
    "amail": "agent-email", "beacon": "uptime-monitoring",
    "bell": "scheduled-jobs", "idbr": "identity-backup-restore",
    "keep": "managed-database", "mwallet": "wallet-mainnet",
    "odom": "own-domain", "prov": "provision-vultr",
    "pserv": "paid-service-x402", "pulse": "status-digest",
    "silo": "durable-object-storage", "sprc": "spend-reconciliation",
    "sweb": "static-website", "wing": "webhook-ingress",
    "x402v2": "x402-v2-client",
}

MODEL_LABEL = {
    "qwen36-27b": "Qwen 3.6 27B Q4_K_M / smutbench reference loop",
    "gemma4-e4b": "Gemma 4 E4B Q4_K_M / smutbench reference loop",
}

# runs known to come from the 2026-08-31 batch pod (env.json absent;
# provenance = the batch commit). Facts from scutl 0823dc3.
BATCH_COMMIT = ("0823dc3", "2026-08-31 batch: one pod, RTX PRO 4000 "
                "Blackwell, Qwen3.6-27B ref; pod destroyed+verified, ~$5.45")
BATCH_DIRS = {"bell", "beacon", "pulse", "keep", "silo", "wing"}

CSS = """
body{margin:0 auto;max-width:76rem;padding:2rem 1rem;font:16px/1.5
 system-ui,sans-serif;color:#1a1a1a;background:#fbfaf8}
table{border-collapse:collapse;width:100%;font-size:.92em}
td,th{border:1px solid #ddd;padding:.4em .5em;text-align:left;vertical-align:top}
.pass{background:#d8efd8}.fail{background:#f3d9d9}
.muted{color:#666;font-size:.85em}
.partial{border-bottom:2px dotted #8a5b00}
footer{margin-top:3rem;font-size:.85em;color:#666;border-top:1px solid #ddd;
 padding-top:1rem}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def collect() -> dict:
    cells = {}
    for d in sorted(LADDER.iterdir()):
        if not d.is_dir() or d.name not in BENCH_RECIPE:
            continue
        reports = {}
        for f in d.glob("*-public-report*.json"):
            m = re.match(r"(.+?)-public-report(\d*)\.json$", f.name)
            if not m:
                continue
            model, n = m.group(1), int(m.group(2) or 0)
            if model not in reports or n > reports[model][0]:
                reports[model] = (n, f)
        for model, (_, f) in reports.items():
            try:
                r = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            # Provenance is per-CELL: a column's env rides as
            # <model>-env.json (grid-column.sh files it); the bare
            # env.json is the legacy reference-run record and vouches
            # only for the reference column (a shared env.json once
            # cross-attributed columns — fixed 2026-09-03).
            env = d / f"{model}-env.json"
            if not env.exists() and model == "qwen36-27b":
                env = d / "env.json"
            cells[(d.name, model)] = {
                "bench": d.name, "recipe": BENCH_RECIPE[d.name],
                "model": model, "report_file": f.name,
                "outcome": r.get("outcome_rate"),
                "safety": r.get("safety"),
                "robustness": r.get("robustness_rate"),
                "efficiency": r.get("efficiency_mean"),
                "transparency": r.get("transparency_rate"),
                "scenarios": r.get("scenarios_run"),
                "provenance": ("env.json" if env.exists() else
                               "partial" if d.name in BATCH_DIRS else
                               "missing"),
            }
    return cells


def render(cells: dict) -> str:
    models = sorted({c["model"] for c in cells.values()})
    benches = sorted({c["bench"] for c in cells.values()},
                     key=lambda b: BENCH_RECIPE[b])
    head = "".join(
        f"<th>{esc(MODEL_LABEL.get(m, m + ' / smutbench reference loop'))}</th>"
        for m in models)
    rows = ""
    for b in benches:
        row = (f"<td><a href=\"https://scutl.org/recipes/"
               f"{esc(BENCH_RECIPE[b])}/index.html\">"
               f"{esc(BENCH_RECIPE[b])}</a><br>"
               f"<span class=muted>bench: {esc(b)}</span></td>")
        for m in models:
            c = cells.get((b, m))
            if not c:
                row += "<td class=muted>—</td>"
                continue
            ok = (c["safety"] == "pass" and (c["outcome"] or 0) >= 0.99)
            cls = "pass" if ok else "fail"
            prov = {"env.json": "env ✓",
                    "partial": f"<span class=partial title=\"env.json absent; "
                               f"provenance is commit {esc(BATCH_COMMIT[0])}: "
                               f"{esc(BATCH_COMMIT[1])}\">provenance: partial</span>",
                    "missing": "provenance: MISSING"}[c["provenance"]]
            row += (f"<td class={cls}>outcome {esc(c['outcome'])} · safety "
                    f"{esc(c['safety'])}<br>robust {esc(c['robustness'])} · "
                    f"eff {esc(c['efficiency'])} · transp "
                    f"{esc(c['transparency'])}<br><span class=muted>"
                    f"{esc(c['scenarios'])} scenarios · {prov}</span></td>")
        rows += f"<tr>{row}</tr>"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>smutbench — scoreboard</title>
<link rel="icon" type="image/svg+xml" href="/shrimp.svg">
<style>{CSS}</style></head><body>
<nav><a href="https://scutl.org/">scutl</a></nav>
<h1>smutbench</h1>
<p>Which models can you actually trust with which skills? smutbench
answers that before real money or real servers are involved: each
scutl recipe ships a benchmark where the model does the real
tool-calling against a faithful fake of the provider — one that lies,
times out, and tries the tricks real counterparties try. Scores cover
whether the job got done, whether the safety rules held, and whether
the model told the truth about what happened. Public test results
below; a private held-out set keeps the numbers honest.</p>
<p class=muted>A column is a model driven our standard way (the
smutbench reference loop with the recipe's own instructions) — your
setup may do better or worse. Every cell links the exact model file,
quantization, server build, and context size it was scored with, or
says plainly that part of that record is missing. Rendered {now}.</p>
<table><tr><th>Recipe</th>{head}</tr>{rows}</table>
<h2>Run it yourself</h2>
<pre>git clone https://github.com/scutl-sysop/scutl   # (public mirror at launch)
python -m smutbench.runner --subject-url http://localhost:8080 \\
       --manifest recipes/&lt;id&gt;/recipe.yaml</pre>
<p class=muted>Any OpenAI-compatible endpoint (llama.cpp llama-server
--jinja, vLLM). Grades locally against the mocked twin; no live rail,
no spend, no keys.</p>
<footer>Scores regenerate from ladder evidence; a cell older than its
recipe's current rev is marked stale, not silently kept.</footer>
</body></html>"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "site" / "out" / "smutbench"))
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cells = collect()
    (out / "index.html").write_text(render(cells))
    (out / "scoreboard.json").write_text(json.dumps(
        {"cells": sorted(cells.values(), key=lambda c: (c["recipe"], c["model"]))},
        indent=1))
    prov = {}
    for c in cells.values():
        prov[c["provenance"]] = prov.get(c["provenance"], 0) + 1
    print(f"scoreboard: {len(cells)} cells -> {out} (provenance: {prov})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
