# Recon: the catalog goes live (scutl.org + smutbench)

Status: DRAFT for Conway review — 2026-09-03, Star.
Source rulings (Conway, live 2026-09-01): site carries fully-baked
recipes; agent interface to same; smutbench on a subdomain; task ×
model grid WITHOUT held-out rounds; tier-1 self-serve only ("swap the
model, keep our harness" — the implicit scutl advert; no BYO-harness
test matrix); "point your agent at the recipe" IS the product
(ADAPT.md pattern, proven by fresh-agent runs, cst-q03b); launch
free, receipts-forward.

## 1. Shape

Three surfaces, one static site generator, zero dynamic backend:

- **scutl.org** — the catalog. Front page: SHIPPED recipes only
  (wallet-base-sepolia, paid-service-x402, provision-vultr today).
  Reference-green recipes listed under a clearly-marked "graded, not
  yet live-proven" section. Every recipe page RENDERS recipe.yaml
  (decide tree, walls, contracts, failure modes) rather than
  hand-written copy that drifts, plus its ADAPT.md and links to run
  receipts. The pitch is one sentence: "Tell your agent: install
  scutl recipe #N. It knows how."
- **Agent interface** — stable URLs, no API: `/recipes/index.json`,
  `/recipes/<id>.yaml`, `/recipes/<id>/ADAPT.md`, `/llms.txt`.
  Content-addressed integrity below (§4).
- **smutbench.scutl.org** — methodology, runner docs (tier-1
  invocation: `python -m smutbench.runner --subject-url ...`), and
  the scoreboard. Held-out rounds stay private — they are the
  verification lever behind every public number.

## 2. The grid (the one real launch cost)

A grid cell = (recipe, model+harness pair, date, env provenance).
Column label is always "model / smutbench reference loop" — a model
alone is a category error (Conway ruling: our harness is the
standard; a fan's own-harness delta is the recipe's prompt-lowering
value, unmeasured by us).

Today the grid is one column (Qwen 3.6 27B reference) plus scattered
headline runs. A credible launch grid wants ~4-6 columns people
actually run. Cost model from our own ladder history: one 4090-class
pod ≈ $0.74/hr; a full public-menu grade of one recipe ≈ 0.5-2h
subject-dependent. Six recipes × 5 new columns × ~1h avg ≈ 30
pod-hours ≈ **$25-45 total** (batching several recipes per pod-up
amortizes bring-up, as the 2026-08-31 six-recipe batch proved).
COLUMN SLATE (Conway-ratified 2026-09-03): (1) Qwen 3.6 27B Q4 —
the reference, already graded, stays the catalog-wide must-green
reference (no reference churn); (2) Gemma E4B (small-open + town
dogfood); (3) Qwen 3.8 27B Q4 on a 5090 (market reality: the
2x3090 -> 5090 crowd's rig); (4) Qwen 3.8 27B FP8 native on RTX 6000
Blackwell (reach; with #3 forms the quantization axis — does Q4
damage recipe SAFETY or just eloquence? — a finding nobody else
publishes). 70B-class dropped (dead zone); closed-API deferred
post-launch (provenance + key dependency); no deliberate tiny-red
column — E4B's natural reds on hard recipes carry the honesty, and
we revisit only if the grid comes back suspiciously all-green.
This campaign is the natural first load for recipe #13 (gpod) —
build the rental walls, then run the campaign on them (dogfood
doubles as #13's live acceptance).

## 3. Launch cut (v1)

IN: catalog pages rendered from recipe.yaml; ADAPT.md per launch
recipe (done ×3 when the two fresh-agent runs land); receipts linked;
grid with honest column count (even 2-3 at launch beats waiting);
smutbench methodology page; live status page (publish the pulse
digest — our own monitoring recipes watching our own infra, honest
attention rows included); llms.txt + index.json; scutl.org beacon
target (legitimizes the existing monitor in the prober account).

OUT (deliberately): accounts, comments, submission portal, community
scoreboard (quarantined community section is a later decision),
BYO-harness runner, any paid endpoint (sell nothing at launch;
credibility is the asset), held-out anything.

## 4. Trust story

- Recipes carry rev numbers; the site publishes a changelog per
  recipe (git history renders this for free).
- Integrity: git tag per release + a signed SHA-256SUMS over
  /recipes/*.yaml and ADAPT.md files, linked from the index. Agents
  asked to execute what they download deserve a checksum path before
  someone asks why there isn't one.
- Every score links env.json (model sha, quant, ctx, harness commit,
  date). No provenance, no cell.
- Site itself: static host + TLS; beacon watches it from outside
  (grid staleness SLA: a cell older than the recipe's current rev is
  marked stale, not silently kept).

## 5. Build plan (beads, in order)

1. cst-q03b (open): finish ADAPT.md ×3 with acceptance runs — in
   flight today.
2. Site generator: recipe.yaml -> HTML renderer + index.json/llms.txt
   emitter (static-website recipe's sweb component is prior art; this
   composes it rather than re-deciding hosting).
3. Status page: pulse digest JSON -> public page (strips nothing; the
   attention rows ARE the demo).
4. smutbench methodology page + scoreboard generator from
   ladder/*/report.json (provenance-or-no-cell enforced in the
   generator).
5. Recipe #13 gpod (manifest -> component), then
6. The grid campaign on gpod rails (Conway spend approval: ~$25-45
   pod budget + any closed-model token spend).
7. Launch checklist: DNS, TLS, beacon target, SHA-256SUMS + tag,
   Conway's go.

## 6. Open questions for Conway

- smutbench branding: own-thing vs strictly scutl QA (recommend:
  present as scutl's method at launch, revisit if outsiders ask to
  submit).
- Status page: full digest or curated? (Recommend full — honest
  degradation is the demo.)
- Domain layout confirmed? (scutl.org + smutbench.scutl.org assumed.)
