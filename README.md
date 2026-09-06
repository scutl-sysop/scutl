# scutl

**Real-world skills. Hard limits.**

scutl packages real-world capabilities for local AI agents — wallets,
servers, paid services, monitoring, backups — as **code-enforced
recipes**: the spending caps, allowlists, and teardown guarantees are
enforced by the installed CLI *before any API call leaves the box*,
not by prompt text the model can talk its way around.

[scutbench](https://scutbench.scutl.org/) is the evidence layer:
which local models can actually use those capabilities safely, graded
on real tool-calling against mock providers that lie, time out, and
try the tricks real counterparties try — before money, infrastructure,
or identity is at risk.

- **Site / catalog:** https://scutl.org — every recipe's manifest,
  rendered and served verbatim, checksummed and ssh-signed
- **Benchmark grid:** https://scutbench.scutl.org
- **Agent front door:** https://scutl.org/llms.txt — point your agent
  at a shipped recipe's `ADAPT.md` and it installs itself
- **Two-minute proof (nothing at stake):**
  https://scutl.org/start-here.html

## Try it

```bash
git clone https://github.com/murdarch/scutl
cd scutl
./tools/first-proof.sh http://localhost:8080   # your local OpenAI-compatible server
```

20 mocked scenarios, no model spend, no accounts — proves your
harness + model can drive a recipe's tools before anything real is
attached.

## What's in a recipe

Each recipe is a molecule, and every part is in this repo:

| Part | What it is |
| --- | --- |
| `recipes/<id>/recipe.yaml` | The manifest: decision tree, code-enforced walls, provider contracts, acceptance tests. The manifest **is** the recipe page on scutl.org. |
| `recipes/<id>/<cli>/` | The typed CLI component that enforces the walls (exit-code taxonomy is the protocol) |
| `recipes/<id>/ADAPT.md` | Agent-facing installer, proven by fresh-agent acceptance runs |
| `scutbench/` *(renaming to scutbench)* | The mocked-twin benchmark deriving scenarios from each manifest's failure modes |
| `ladder/` | The live-rail run harness and receipts behind every Shipped badge |
| `site/` | The static site generator — pages render from manifests, so the site cannot drift from the recipes |

## The trust ladder

Recipes are grouped by exposure, and the intended path is to climb:
disposable/testnet first, then read-only, then act-with-approval,
then hard-capped unattended action, and only then real money and
persistent infrastructure. Every claim on the site links its
evidence: graded reports, on-chain transactions, signed checksums.

## Status honesty rules

The site never claims more than the artifacts support: a shipped
recipe without an installer says so, cells without a pinned
environment record wear a visible partial-provenance marker, and the
held-out benchmark rounds that keep the public numbers honest stay
private.
