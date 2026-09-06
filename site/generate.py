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

# recipe slug -> bench dir, for the grid backlink (reverse of the
# scoreboard's BENCH_RECIPE — cst-ldf8: link recipe <-> its grid row)
from scoreboard import BENCH_RECIPE  # noqa: E402
RECIPE_BENCH = {v: k for k, v in BENCH_RECIPE.items()}
RECIPES = ROOT / "recipes"
COPY = yaml.safe_load((ROOT / "site" / "copy.yaml").read_text())

STATUS_ORDER = {"shipped": 0, "reference-green": 1, "draft": 2}

# Progressive risk grouping + factual chips (cst-plem, Sun's persona
# review): risk is the primary browse order, maturity stays a badge.
RISK = yaml.safe_load((ROOT / "site" / "risk.yaml").read_text())

# recipe slug -> receipts/ subtree holding its live run evidence
# (cst-ravb: receipts are served, not merely claimed)
RECEIPT_DIR = {
    "wallet-mainnet": "mwallet",
    "wallet-base-sepolia": "wallet",
    "paid-service-x402": "paid-service",
    "provision-vultr": "provision",
}
STATUS_LABEL = {
    "shipped": "Shipped — manifest, enforcing CLI, agent installer "
               "(ADAPT.md), live-proven",
    "reference-green": "Benchmarked — model results on the grid, "
                       "not yet live-proven",
    "draft": "Draft — design published; components vary, some already "
             "on the benchmark grid",
}

def status_label(r: dict) -> str:
    """Honest per-recipe label: a shipped recipe without an ADAPT.md
    must not claim the agent installer it doesn't serve (cst-ahk1
    finding 8 — trust labels contradicting artifacts)."""
    st = r["status"]
    if st == "shipped" and not r["adapt"]:
        return ("Shipped — manifest, enforcing CLI, live-proven; "
                "agent installer (ADAPT.md) pending, not installable yet")
    return STATUS_LABEL.get(st, st)


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
.chips{display:inline-flex;flex-wrap:wrap;gap:.35em;margin-top:.25em}
.chip{display:inline-block;padding:.05em .5em;border:1px solid #ddd6c8;
 border-radius:1em;font-size:.78em;color:#555;background:#f6f4ef}
.chip b{font-weight:600;color:#333}
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
        f"{body}<footer>Shipped recipes link their run receipts from "
        "their pages. "
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
            f"{esc(r['rev'])} · {esc(status_label(r))}</p>"
            + lede
            + f"<p><a href=\"../{esc(r['slug'])}.yaml\">manifest (yaml)</a>"
            + (f" · <a href=\"ADAPT.md\">ADAPT.md — point your agent here"
               f"</a>" if r["adapt"] else "")
            + (f" · <a href=\"../../receipts/"
               f"{esc(RECEIPT_DIR[r['slug']])}/index.html\">run receipts"
               f"</a>" if r["slug"] in RECEIPT_DIR else "")
            + (f" · <a href=\"https://smutbench.scutl.org/\">benchmark "
               f"results (bench: {esc(RECIPE_BENCH[r['slug']])})</a>"
               if r["slug"] in RECIPE_BENCH else "")
            + "</p>")
    exec_block = m.get("execute") or {}
    guard = exec_block.get("guardrails") or []
    guard_html = ("<h2>Guardrails (carried verbatim into the agent)</h2><ul>"
                  + "".join(f"<li>{esc(' '.join(str(g).split()))}</li>"
                            for g in guard) + "</ul>") if guard else ""
    body = (head + render_decide(m) + render_components(m)
            + render_failures(m) + guard_html)
    return page(f"{r['title']} — scutl", body, depth=2)


def chips_html(slug: str) -> str:
    chips = ((RISK.get("recipes") or {}).get(slug) or {}).get("chips") or {}
    if not chips:
        return ""
    order = ["money", "authority", "attendance", "data", "reversibility"]
    spans = "".join(
        f"<span class=chip><b>{esc(k)}</b> {esc(str(chips[k]))}</span>"
        for k in order if k in chips)
    return f"<span class=chips>{spans}</span>"


def index_page(recipes: list[dict]) -> str:
    def line(r):
        c = (COPY.get("recipes") or {}).get(r["slug"]) or {}
        desc = c.get("hook") or (r["summary"][:180]
                                 + ("…" if len(r["summary"]) > 180 else ""))
        badge = (f"<span class=\"status status-{esc(r['status'])}\">"
                 f"{esc(r['status'])}</span>")
        return (f"<li><a href=\"recipes/{esc(r['slug'])}/index.html\">"
                f"{esc(r['title'])}</a> <span class=muted>({esc(r['id'])})"
                f"</span> {badge}<br>{esc(desc)}<br>"
                f"{chips_html(r['slug'])}</li>")

    by_slug = {r["slug"]: r for r in recipes}
    assigned: set[str] = set()
    sections = []
    for g in RISK.get("groups") or []:
        slugs = [s for s, meta in (RISK.get("recipes") or {}).items()
                 if meta.get("group") == g["id"] and s in by_slug]
        rs = sorted((by_slug[s] for s in slugs),
                    key=lambda r: (STATUS_ORDER.get(r["status"], 9),
                                   r["slug"]))
        assigned.update(slugs)
        if not rs:
            continue
        blurb = " ".join(str(g.get("blurb", "")).split())
        sections.append(f"<h2>{esc(g['title'])}</h2>"
                        f"<p class=muted>{esc(blurb)}</p>"
                        f"<ul>{''.join(line(r) for r in rs)}</ul>")
    # A recipe missing from risk.yaml stays visible — loudly, never hidden
    leftovers = sorted((r for r in recipes if r["slug"] not in assigned),
                       key=lambda r: r["slug"])
    if leftovers:
        sections.append("<h2>Ungrouped (risk metadata pending)</h2><ul>"
                        + "".join(line(r) for r in leftovers) + "</ul>")
    hero = COPY.get("hero") or {}
    body = (f"<h1>{esc(hero.get('headline','scutl'))}</h1>"
            f"<p class=pitch>{esc(' '.join(str(hero.get('sub','')).split()))}</p>"
            f"<p>{esc(' '.join(str(hero.get('how','')).split()))}</p>"
            "<p><strong><a href=\"start-here.html\">Start here</a></strong> "
            "— what a harness is, a two-minute proof with nothing at "
            "stake, and the first safe rung. Already harnessed? Tell "
            "your agent: <em>fetch "
            "https://scutl.org/recipes/wallet-base-sepolia/ADAPT.md "
            "and follow it.</em></p>" + "".join(sections))
    return page("scutl — recipes for agent life skills", body)


def start_here_page() -> str:
    """The onboarding route (cst-y9kx, Sun's persona review): explain
    the pieces, prove the pipeline with nothing at stake, then climb."""
    body = """
<h1>Start here</h1>
<p class=pitch>You have a local model running — maybe llama.cpp,
Ollama, or vLLM behind a chat UI — and you want it to actually
<em>do</em> things: hold money, rent servers, keep backups, without
trusting it not to mess up. That is exactly what scutl recipes are
for. No programming required; you will run a few terminal commands and
approve the consequential ones.</p>

<h2>1 · The three pieces</h2>
<table>
<tr><td><strong>Your local model</strong></td><td>the reasoning. Any
model behind an OpenAI-compatible endpoint (llama.cpp
<code>llama-server --jinja</code>, vLLM, Ollama's <code>/v1</code>).
The <a href="https://smutbench.scutl.org/">benchmark grid</a> shows
how tested models handle each recipe.</td></tr>
<tr><td><strong>A harness</strong></td><td>the hands: something that
lets the model run tools/shell commands, keeps instructions in its
context, and shows you what it's doing. A chat UI alone is usually
NOT a harness — OpenWebUI out of the box only chats; its tool runner
must be configured before it counts. Anything that can run shell
commands and carry a system prompt works: Claude Code, Codex, Open
Interpreter, an OpenWebUI tool server, or your own loop.
<strong><a href="harnesses.html">Get a harness</a></strong> — a
shortest path plus alternatives, each with install steps, the three
endpoint values, and a preflight that proves it has hands.</td></tr>
<tr><td><strong>A scutl recipe</strong></td><td>the guard rails:
careful instructions plus a typed CLI that enforces the limits in
code — spending caps, approval gates, teardown checks. The model can
propose; the code decides. Your harness cannot weaken that and
doesn't need to implement it.</td></tr>
</table>

<h2>2 · Prove the pipeline — nothing at stake</h2>
<p>One command runs a real benchmark against a <em>fake</em>
provider: no account, no key, no payment, nothing persistent. It
includes scenarios where the only correct move is to refuse, so a
pass means the walls held, not just that the task got done.</p>
<pre>git clone https://github.com/scutl-sysop/scutl
cd scutl
./tools/first-proof.sh                     # no model needed: proves
                                           # the pipeline end to end
./tools/first-proof.sh http://localhost:8080   # grades YOUR model</pre>
<p class=muted>It ends with a plain PASS or a plain list of what
failed. Delete <code>.first-proof-venv/</code> afterward and nothing
remains.</p>

<h2>3 · First real rung: the testnet wallet</h2>
<p><strong>Free · testnet only · creates a local key · short human
ceremony.</strong> Your agent holds test-money USDC with a spending
cap it cannot lift, and buys a real x402-priced resource — every
motion of the real-money recipe, nothing at stake. Point your
harnessed agent at the installer and it does the rest, pausing for
your approvals:</p>
<pre>Fetch https://scutl.org/recipes/wallet-base-sepolia/ADAPT.md and follow it.</pre>

<h2>4 · Then climb</h2>
<p>The <a href="index.html">catalog</a> is ordered by exposure: read-only
recipes first, then reversible acts with you at the consequential
step, then unattended action under hard caps, and only at the bottom
real money and persistent infrastructure. Every card's chips say what
a recipe can spend, touch, and undo — climb at whatever pace your
trust has earned.</p>
"""
    return page("Start here — scutl", body)


def harnesses_page() -> str:
    """Per-harness install guides (cst-y9kx, Sun's route step 2): one
    blessed shortest path plus secondaries, each with install, the
    three local-endpoint values, and a preflight proving shell access,
    tool calling, system-context persistence, and exit-code
    visibility. Honesty rule carried from the rest of the site: each
    guide states exactly what we have verified ourselves."""
    body = """
<h1>Get a harness</h1>
<p class=pitch>A harness is the part that gives your model hands:
shell commands, files, tool calls, persistent instructions, and a
place for you to approve the consequential steps. Your chat UI is
probably not one — this page gets you one, points it at the model you
already run, and proves it works before anything is at stake.</p>

<h2>The three values every harness asks for</h2>
<p>Whatever server runs your model, a harness needs exactly three
things about it:</p>
<table>
<tr><th></th><th>Base URL</th><th>Model id</th><th>API key</th></tr>
<tr><td><strong>llama.cpp</strong> (<code>llama-server --jinja</code>)</td>
<td><code>http://localhost:8080/v1</code></td>
<td>whatever <code>/v1/models</code> reports (often the GGUF name;
many builds accept any string)</td>
<td>any non-empty string, unless you started the server with
<code>--api-key</code></td></tr>
<tr><td><strong>Ollama</strong></td>
<td><code>http://localhost:11434/v1</code></td>
<td>the tag you pull, e.g. <code>qwen3:32b</code></td>
<td>any non-empty string</td></tr>
<tr><td><strong>vLLM</strong></td>
<td><code>http://localhost:8000/v1</code></td>
<td>the <code>--served-model-name</code> you launched with</td>
<td>any non-empty string, unless launched with
<code>--api-key</code></td></tr>
</table>
<p class=muted>Check the first two in one line before touching any
harness: <code>curl -s http://localhost:8080/v1/models</code> should
return JSON naming your model. If it doesn't, fix that first —
no harness can help.</p>

<h2 id=preflight>The preflight — run this in ANY harness before any
recipe</h2>
<p>Paste this to your harnessed agent, verbatim:</p>
<pre>Preflight, four checks. (1) Run the shell command
`echo scutl-shell-ok; exit 42` and report the exact stdout and the
exact numeric exit code. (2) Write a file scutl-preflight.txt
containing the word HELD, read it back to me, then delete it.
(3) From now on, begin every reply with the word ANCHOR.
(4) Tell me which tool you used for each step.</pre>
<p>It passes only if: stdout is <code>scutl-shell-ok</code> and the
exit code is reported as the number 42 (not "it failed"); the file
round-trips; <em>subsequent</em> replies still start with ANCHOR
(ask it something unrelated to check); and it names real tools. An
agent that answers from imagination — wrong exit code, success
without running anything, ANCHOR forgotten two turns later — is
chat, not hands. Recipes put their walls in code your harness runs;
a harness that can't run code or see exit codes holds no walls.</p>

<h2>Shortest path: Codex CLI against your local server</h2>
<p><strong>Free · open source · runs your model, not a cloud
one.</strong> Codex is a terminal harness with shell tools and
approval prompts, and it speaks to any OpenAI-compatible endpoint.</p>
<pre>npm install -g @openai/codex      # or: brew install --cask codex</pre>
<p>Then put your three values in <code>~/.codex/config.toml</code>:</p>
<pre>model = "YOUR-MODEL-ID"           # value two
model_provider = "local"

[model_providers.local]
name = "my local server"
base_url = "http://localhost:8080/v1"   # value one
wire_api = "chat"                 # local servers speak chat, not
                                  # the Responses API</pre>
<p>Local servers that don't check keys need no key entry; if yours
does, add <code>env_key = "LOCAL_API_KEY"</code> and export that
variable (value three). Start it with <code>codex</code>, run the
preflight above, then prove the whole pipeline with nothing at
stake:</p>
<pre>git clone https://github.com/scutl-sysop/scutl
cd scutl
./tools/first-proof.sh http://localhost:8080</pre>
<p class=muted>Verified by us: the install commands are current
upstream (checked 2026-09-05) and first-proof.sh passes end-to-end
from a clean public clone. Not yet verified by us: a full
Codex-against-llama.cpp session on our own hardware — the config
format above is from Codex's own reference; if your Codex version
rejects it, its bundled config docs win.</p>

<h2>Alternative: Hermes Agent (Nous Research)</h2>
<p><strong>Free · open source · MIT.</strong> If you're here from the
Hermes community you may already run it. It is a real harness — shell
tools, files, approval flow — and it is what drives every graded cell
on <a href="https://smutbench.scutl.org/">the smutbench grid</a>: our
ladder invokes <code>hermes</code> against a llama.cpp pod for each
rep.</p>
<pre>curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash</pre>
<p>Point it at your local server (value one), and it can read the
model id off the server itself:</p>
<pre>hermes config set model.base_url http://localhost:8080/v1
hermes -m YOUR-MODEL-ID    # or omit -m and pick interactively</pre>
<p>One gotcha we hit in production: hermes tool shells rebuild PATH
from your login profile, so PATH changes made mid-session never reach
the model's commands. Install any CLI a recipe needs into
<code>~/.local/bin</code> (first on the profile PATH) and the ADAPT
steps work as written.</p>
<p class=muted>Verified by us: daily ladder use against our own
llama.cpp pods — <code>config set model.base_url</code>, tool
calling, exit-code visibility, the PATH gotcha and its fix. The
install command is from upstream's install docs (checked 2026-09-06);
we installed from an earlier snapshot, so if the installer's questions
differ, upstream's docs win.</p>

<h2>Alternative: Oh My Pi (omp)</h2>
<p><strong>Free · open source.</strong> A terminal coding agent with
LSP integration and Agent Skills support. We run omp 17.2.12 on the
same machine that publishes this site; the provision-vultr reference
integration in <a
href="https://github.com/scutl-sysop/scutl">the repo</a>
(<code>integrations/omp/</code>) was built and verified against
it.</p>
<pre>bun install -g @oh-my-pi/pi-coding-agent
# or: brew install can1357/tap/omp
# or: curl -fsSL https://omp.sh/install | sh</pre>
<p>Local server: declare a provider in
<code>~/.omp/agent/models.yml</code> with your three values, then
select it:</p>
<pre>providers:
  local:
    baseUrl: http://localhost:8080/v1   # value one
    api: openai-completions
    apiKey: dummy                       # value three
    models:
      - id: YOUR-MODEL-ID               # value two
        name: my local model
        contextWindow: 65536
        maxTokens: 8192</pre>
<p>Verify with <code>omp models local</code>, pick it with <code>omp
setup</code>, and note omp exposes a generic bash tool rather than
per-command gating — recipes' walls still hold (they live in the
installed code, not the harness), but approvals are coarser:
<code>--approval-mode</code> takes <code>always-ask</code>,
<code>write</code>, or <code>yolo</code>.</p>
<p class=muted>Verified by us: omp 17.2.12 installed and exercised on
our own hardware — skills discovery from
<code>~/.agents/skills/</code>, the <code>--skills</code> /
<code>--no-skills</code> flags, and the reference integration's
install steps. The models.yml provider format is from upstream's
README (checked 2026-09-06); not yet re-run against a local
llama.cpp endpoint on our hardware.</p>

<h2>Alternative: Claude Code (subscription)</h2>
<p>If you already pay for Claude, Claude Code is a fully proven
harness — it is what we run our own acceptance tests in. The catch
for this site's purpose: it drives Anthropic's models, not the one
on your GPU. That still matters here, because a recipe's walls live
in code, not in the model: you can let Claude Code do the one-time
ADAPT.md installation of a recipe, and the installed walls then hold
for <em>any</em> agent you point at them afterwards — including your
local model in another harness.</p>
<pre>npm install -g @anthropic-ai/claude-code
claude</pre>
<p class=muted>Verified by us: daily use, and every fresh-agent
acceptance run behind the Shipped badges. The preflight passes.</p>

<h2>Alternative: OpenWebUI you already have</h2>
<p>Out of the box, OpenWebUI chats — it is the front end you talk
through, not hands. It only becomes a harness once its tool runner /
function-calling is configured to execute real commands, which is its
own project with its own docs. We have not validated a specific
OpenWebUI tool-server path, so we won't pretend to bless one. The
test is not the brand: configure it however you like, and if the
preflight above passes — real stdout, real exit code, ANCHOR
surviving — it counts.</p>

<h2>Alternative: your own loop</h2>
<p>Any ~hundred-line loop that feeds a system prompt, executes tool
calls in a shell, and returns exit codes is a harness. The benchmark
harness in <a
href="https://github.com/scutl-sysop/scutl">the repo</a>
(<code>smutbench/</code>) is a working reference: it is exactly what
graded every cell on <a
href="https://smutbench.scutl.org/">the grid</a>.</p>

<h2>Then</h2>
<p>Back to <a href="start-here.html">Start here</a>: run the
disposable proof, then the testnet wallet. The
<a href="index.html">catalog</a> is ordered by exposure when you're
ready to climb.</p>
"""
    return page("Get a harness — scutl", body)


def llms_txt(recipes: list[dict]) -> str:
    lines = [
        "# scutl — agent capability recipes",
        "",
        "A recipe = manifest (decision tree, code-enforced walls,",
        "provider contracts, acceptance tests) + typed CLI component +",
        "mocked-twin benchmark. SHIPPED recipes carry an ADAPT.md,",
        "addressed to you: fetch it and follow it to integrate the",
        "recipe into your harness. Entries without an ADAPT.md listed",
        "below are not installable yet — read the manifest, don't 404.",
        "",
        "Index: /recipes/index.json (sha256 per manifest; verify",
        "against /SHA-256SUMS, ssh-signed by /allowed_signers:",
        "ssh-keygen -Y verify -f allowed_signers -I scutl-release",
        "-n file -s SHA-256SUMS.sig < SHA-256SUMS).",
        "",
    ]
    for r in recipes:
        st = r["status"]
        if st == "shipped" and not r["adapt"]:
            st = "shipped, ADAPT pending — not installable yet"
        lines.append(f"- {r['id']} rev {r['rev']} [{st}]: "
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

    # Receipts: copy each shipped recipe's run-evidence tree and give it
    # a browsable index (the host serves no directory listings).
    slug_by_dir = {v: k for k, v in RECEIPT_DIR.items()}
    for rdir in sorted(set(RECEIPT_DIR.values())):
        src = ROOT / "receipts" / rdir
        if not src.is_dir():
            continue
        dst = out / "receipts" / rdir
        shutil.copytree(src, dst)
        items = []
        for f in sorted(dst.rglob("*")):
            if f.is_file():
                rel = f.relative_to(dst)
                items.append(f"<li><a href=\"{esc(str(rel))}\">"
                             f"{esc(str(rel))}</a> <span class=muted>"
                             f"({f.stat().st_size:,} bytes)</span></li>")
        slug = slug_by_dir[rdir]
        body = (f"<h1>Run receipts — {esc(slug)}</h1>"
                f"<p>Every file below is a live-run record for "
                f"<a href=\"../../recipes/{esc(slug)}/index.html\">"
                f"{esc(slug)}</a>: pinned environment, protocol, and "
                f"per-repetition verdicts, committed as produced. "
                f"Directories are numbered by recipe rev.</p>"
                f"<ul>{''.join(items)}</ul>")
        (dst / "index.html").write_text(
            page(f"receipts — {slug}", body, depth=2))

    (out / "index.html").write_text(index_page(recipes))
    (out / "start-here.html").write_text(start_here_page())
    (out / "harnesses.html").write_text(harnesses_page())
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
