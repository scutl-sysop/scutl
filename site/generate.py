#!/usr/bin/env python3
"""scutl.org static site generator (cst-j01t, ratified recon §1/§5.2).

Renders the catalog FROM the manifests — recipe.yaml is the page, not
its source material — so the site cannot drift from the recipes. Emits:

  out/index.html            — catalog front page (shipped first,
                              reference-green under a labeled section,
                              drafts listed but unlinked-from-front)
  out/recipes/<id>/index.html
  out/recipes/<id>.yaml     — the manifest verbatim (agent interface)
  out/recipes/<id>/ADAPT.md — verbatim, when the recipe ships one
  out/recipes/index.json    — machine index (id, title, status, rev,
                              urls, sha256 of the yaml)
  out/llms.txt              — the agent-facing front door
  out/SHA-256SUMS           — integrity over every served yaml/ADAPT

Stdlib + PyYAML only. No JS, no external assets; one inline stylesheet.
Usage: python site/generate.py [--out site/out]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "recipes"
COPY = yaml.safe_load((ROOT / "site" / "copy.yaml").read_text())

STATUS_ORDER = {"shipped": 0, "reference-green": 1, "draft": 2}
STATUS_LABEL = {
    "shipped": "Shipped — live-proven, run receipts attached",
    "reference-green": "Reference-green — graded, not yet live-proven",
    "draft": "Draft — design published, not yet graded",
}

CSS = """
body{margin:0 auto;max-width:72rem;padding:2rem 1rem;font:16px/1.55
 system-ui,sans-serif;color:#1a1a1a;background:#fbfaf8}
h1,h2,h3{line-height:1.2} a{color:#0a5c9e}
code,pre{font-family:ui-monospace,monospace;font-size:.92em;
 background:#f0ede8;border-radius:4px}
pre{padding:1rem;overflow-x:auto} code{padding:.1em .3em}
.status{display:inline-block;padding:.15em .6em;border-radius:1em;
 font-size:.8em;font-weight:600;vertical-align:middle}
.status-shipped{background:#d8efd8;color:#1d5c1d}
.status-reference-green{background:#e3ecf7;color:#1d3f6e}
.status-draft{background:#eee8dc;color:#6e5c1d}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ddd;padding:.4em .6em;text-align:left;
 vertical-align:top}
.muted{color:#666} .pitch{font-size:1.15em}
footer{margin-top:3rem;font-size:.85em;color:#666;
 border-top:1px solid #ddd;padding-top:1rem}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


BUILD_SERIAL = None  # set in main(); rendered into every page footer


def page(title: str, body: str, depth: int = 0) -> str:
    home = "../" * depth or "./"
    serial = f"<!-- beacon serial={BUILD_SERIAL} -->" if BUILD_SERIAL else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        f"<nav><a href=\"{home}index.html\">scutl</a> · "
        f"<a href=\"https://smutbench.scutl.org\">smutbench</a> · "
        f"<a href=\"{home}llms.txt\">llms.txt</a></nav>"
        f"{body}<footer>Every claim on this site links a receipt. "
        "Manifests are the pages: rendered from recipe.yaml at build "
        "time, served verbatim next to them, checksummed in "
        f"<a href=\"{home}SHA-256SUMS\">SHA-256SUMS</a>.</footer>"
        f"{serial}</body></html>"
    )


def load_recipes() -> list[dict]:
    out = []
    for d in sorted(RECIPES.iterdir()):
        f = d / "recipe.yaml"
        if not f.is_file():
            continue
        try:
            m = yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            print(f"SKIP {d.name}: unparseable manifest ({e})",
                  file=sys.stderr)
            continue
        r = (m or {}).get("recipe") or {}
        out.append({
            "dir": d, "id": r.get("id", d.name), "slug": d.name,
            "title": ((COPY.get("recipes") or {}).get(d.name, {}).get("title")
                      or r.get("title", d.name)),
            "rev": r.get("rev", "?"),
            "summary": " ".join(str(r.get("summary", "")).split()),
            "status": str(r.get("status", "draft")).split()[0],
            "manifest": m,
            "adapt": (d / "ADAPT.md") if (d / "ADAPT.md").is_file() else None,
        })
    out.sort(key=lambda r: (STATUS_ORDER.get(r["status"], 9), r["slug"]))
    return out


def render_decide(m: dict) -> str:
    rows = []
    for q in m.get("decide") or []:
        blessed = [o for o in q.get("options") or [] if o.get("blessed")]
        label = " ".join(str(blessed[0].get("label", "")).split()) if blessed else "—"
        deferred = ", ".join(q.get("deferred-options") or []) or "—"
        rows.append(f"<tr><td><code>{esc(q.get('id'))}</code><br>"
                    f"<span class=muted>{esc(q.get('question',''))}</span></td>"
                    f"<td>{esc(label)}</td><td class=muted>{esc(deferred)}</td></tr>")
    if not rows:
        return ""
    return ("<h2>Decisions</h2><table><tr><th>Question</th>"
            "<th>Blessed (rev 1)</th><th>Deferred</th></tr>"
            + "".join(rows) + "</table>")


def render_components(m: dict) -> str:
    parts = []
    for name, c in (m.get("components") or {}).items():
        if not isinstance(c, dict):
            continue
        tools = c.get("tools") or []
        trows = "".join(
            f"<tr><td><code>{esc(t.get('name'))}</code></td>"
            f"<td><code>{esc(t.get('command'))}</code></td></tr>"
            for t in tools if isinstance(t, dict))
        inv = "".join(f"<li>{esc(' '.join(str(i).split()))}</li>"
                      for i in c.get("invariants") or [])
        parts.append(
            f"<h3><code>{esc(name)}</code> <span class=muted>"
            f"({esc(c.get('kind',''))})</span></h3>"
            + (f"<table><tr><th>Tool</th><th>Invocation</th></tr>{trows}</table>"
               if trows else "")
            + (f"<h4>Enforced in code</h4><ul>{inv}</ul>" if inv else ""))
    return "<h2>Component</h2>" + "".join(parts) if parts else ""


def render_failures(m: dict) -> str:
    rows = []
    for dep, c in (m.get("contracts") or {}).items():
        if isinstance(c, dict) and c.get("failure_modes"):
            fm = ", ".join(f"<code>{esc(x)}</code>" for x in c["failure_modes"])
            rows.append(f"<tr><td><code>{esc(dep)}</code></td><td>{fm}</td></tr>")
    if not rows:
        return ""
    return ("<h2>Failure modes the bench grades</h2><table>"
            "<tr><th>Dependency</th><th>Modes</th></tr>"
            + "".join(rows) + "</table>")


def recipe_page(r: dict) -> str:
    m = r["manifest"]
    st = r["status"]
    badge = f"<span class=\"status status-{esc(st)}\">{esc(st)}</span>"
    c = (COPY.get("recipes") or {}).get(r["slug"]) or {}
    lede = (f"<p class=pitch><strong>{esc(c['hook'])}</strong></p>"
            f"<p>{esc(' '.join(str(c.get('blurb','')).split()))}</p>"
            if c.get("hook") else f"<p class=pitch>{esc(r['summary'])}</p>")
    head = (f"<h1>{esc(r['title'])} {badge}</h1>"
            f"<p class=muted>recipe <code>{esc(r['id'])}</code> · rev "
            f"{esc(r['rev'])} · {esc(STATUS_LABEL.get(st, st))}</p>"
            + lede
            + f"<p><a href=\"../{esc(r['slug'])}.yaml\">manifest (yaml)</a>"
            + (f" · <a href=\"ADAPT.md\">ADAPT.md — point your agent here"
               f"</a>" if r["adapt"] else "")
            + "</p>")
    exec_block = m.get("execute") or {}
    guard = exec_block.get("guardrails") or []
    guard_html = ("<h2>Guardrails (carried verbatim into the agent)</h2><ul>"
                  + "".join(f"<li>{esc(' '.join(str(g).split()))}</li>"
                            for g in guard) + "</ul>") if guard else ""
    body = (head + render_decide(m) + render_components(m)
            + render_failures(m) + guard_html)
    return page(f"{r['title']} — scutl", body, depth=2)


def index_page(recipes: list[dict]) -> str:
    sections = []
    for st in ("shipped", "reference-green", "draft"):
        rs = [r for r in recipes if r["status"] == st]
        if not rs:
            continue
        def line(r):
            c = (COPY.get("recipes") or {}).get(r["slug"]) or {}
            desc = c.get("hook") or (r["summary"][:180]
                                     + ("…" if len(r["summary"]) > 180 else ""))
            return (f"<li><a href=\"recipes/{esc(r['slug'])}/index.html\">"
                    f"{esc(r['title'])}</a> <span class=muted>({esc(r['id'])})"
                    f"</span><br>{esc(desc)}</li>")
        items = "".join(line(r) for r in rs)
        sections.append(f"<h2>{esc(STATUS_LABEL[st])}</h2><ul>{items}</ul>")
    hero = COPY.get("hero") or {}
    body = (f"<h1>{esc(hero.get('headline','scutl'))}</h1>"
            f"<p class=pitch>{esc(' '.join(str(hero.get('sub','')).split()))}</p>"
            f"<p>{esc(' '.join(str(hero.get('how','')).split()))}</p>"
            "<p><strong>Tell your agent: install a scutl recipe. "
            "It knows how.</strong></p>" + "".join(sections))
    return page("scutl — recipes for agent life skills", body)


def llms_txt(recipes: list[dict]) -> str:
    lines = [
        "# scutl — agent capability recipes",
        "",
        "A recipe = manifest (decision tree, code-enforced walls,",
        "provider contracts, acceptance tests) + typed CLI component +",
        "mocked-twin benchmark. To integrate one into your harness,",
        "fetch its ADAPT.md and follow it; it is addressed to you.",
        "",
        "Index: /recipes/index.json (sha256 per manifest; verify",
        "against /SHA-256SUMS).",
        "",
    ]
    for r in recipes:
        lines.append(f"- {r['id']} rev {r['rev']} [{r['status']}]: "
                     f"/recipes/{r['slug']}.yaml"
                     + (f" + /recipes/{r['slug']}/ADAPT.md" if r["adapt"] else ""))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "site" / "out"))
    args = p.parse_args(argv)
    global BUILD_SERIAL
    import time
    BUILD_SERIAL = int(time.time())
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "recipes").mkdir(parents=True)

    recipes = load_recipes()
    sums = []

    for r in recipes:
        d = out / "recipes" / r["slug"]
        d.mkdir()
        (d / "index.html").write_text(recipe_page(r))
        yaml_bytes = (r["dir"] / "recipe.yaml").read_bytes()
        (out / "recipes" / f"{r['slug']}.yaml").write_bytes(yaml_bytes)
        sums.append((f"recipes/{r['slug']}.yaml",
                     hashlib.sha256(yaml_bytes).hexdigest()))
        if r["adapt"]:
            ab = r["adapt"].read_bytes()
            (d / "ADAPT.md").write_bytes(ab)
            sums.append((f"recipes/{r['slug']}/ADAPT.md",
                         hashlib.sha256(ab).hexdigest()))

    (out / "index.html").write_text(index_page(recipes))
    (out / "llms.txt").write_text(llms_txt(recipes))
    (out / "recipes" / "index.json").write_text(json.dumps({
        "generator": "scutl-site rev1", "recipes": [{
            "id": r["id"], "slug": r["slug"], "title": r["title"],
            "rev": r["rev"], "status": r["status"],
            "yaml": f"/recipes/{r['slug']}.yaml",
            "adapt": (f"/recipes/{r['slug']}/ADAPT.md" if r["adapt"] else None),
            "page": f"/recipes/{r['slug']}/index.html",
            "sha256": dict(sums)[f"recipes/{r['slug']}.yaml"],
        } for r in recipes]}, indent=1))
    (out / "SHA-256SUMS").write_text(
        "".join(f"{h}  {path}\n" for path, h in sums))
    print(f"generated {len(recipes)} recipes -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
