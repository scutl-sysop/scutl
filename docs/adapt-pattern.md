# The ADAPT pattern — how a recipe becomes installable

Status: proven in practice (5 recipes drafted, 4 passed fresh-agent
acceptance; wallet-mainnet parked as draft pending a money-moving run).
Bead: cst-q03b. Ruling: Conway, 2026-09-01 — "point your agent at the
recipe" IS the product; there is no harness test matrix, and drop-in
skills are cached outputs of the adapt process, never hand-maintained
ports.

This document is for recipe authors and maintainers. The per-recipe
`ADAPT.md` files are for integrating agents; this one explains how to
write and prove them.

## The shape

A shipped recipe carries exactly one integration document,
`recipes/<id>/ADAPT.md`, addressed in the second person **to the agent
doing the integration** — not to a human, not to a specific harness.
The human's entire integration action is:

> Fetch https://scutl.org/recipes/\<id\>/ADAPT.md and follow it.

The site generator links `ADAPT.md` when present and labels a shipped
recipe without one "agent installer (ADAPT.md) pending, not
installable yet"
(`site/generate.py`). That label is honest by construction: a recipe
whose ADAPT has not passed acceptance keeps the draft filename
(`ADAPT-draft.md`) so the generator never links it — renaming to
`ADAPT.md` is the act of shipping the integration path, so nothing on
the site ever points at an unproven installer.

Every ADAPT.md keeps the same five-step skeleton, because agents that
have seen one recipe should find the next one familiar:

1. **Install the component** — venv, pip from the repo, first probe.
2. **Expose the tools** — map `components.*.tools` into the harness.
3. **Carry the guardrails** — copy `execute.loop` + `execute.guardrails`
   into system context, verbatim.
4. **Setup with the human** — walk `setup:`, honoring the actor/approval
   markers.
5. **Acceptance** — run the `verify:` checks from inside the target
   harness; the recipe is not installed until this passes.

Plus two framing sections: a 30-second orientation up top (what the
safety model is, what failure the recipe exists to prevent, where the
one hard secrecy boundary sits), and "If you get stuck" at the bottom
(a stumble is a documentation bug we want — report step, exact
command, exact error, harness and version).

Two sentences appear in every ADAPT, early: `recipe.yaml` is the
authority and disagreement with it is a reportable bug; and the
acceptance standard is that **a fresh agent completes the document
without outside help**.

## The proof: fresh-agent acceptance

An ADAPT.md is drafted by an insider and therefore full of things the
insider cannot see. The proof procedure removes the insider:

- Spawn a **clean-context agent** — no scutl history, no chat with the
  author. A subagent with an empty context works; so does a separate
  harness session.
- Give it exactly two things: the recipe directory (or its public URL)
  and the sentence *"make this available in \<harness\>"*.
- Give it **zero help**. Watch, don't steer. Every place it stalls,
  guesses wrong, or asks a question is a **doc bug**, not an agent
  failure — fix ADAPT.md, not the agent.
- The run's outputs are evidence, checked in:
  `integrations/<harness>/<recipe>/` holds the integration the agent
  produced (INSTALL.md, skills/tool definitions, system-prompt
  append), and the transcript backs the claim that the doc was
  completed unaided.

This is the same trick as the mocked-twin benches, applied to prose:
you don't trust a document because the author reread it, you trust it
because a stranger executed it. The track record says it earns its
cost — across the drafting and acceptance cycles the pattern surfaced
**3 real code/manifest bugs** (an argparse exit-code collision,
cst-qiru; `prov status` exiting 0 when it shouldn't; `mw_buy`
commanding a `--wallet` flag that doesn't exist) and **20+ doc gaps**, none of
which any insider had noticed.

### When acceptance can't run

Two legitimate blocks, each with its honest reporting form:

- **Blocked-on-harness**: the target harness can't hold a session yet
  (no model auth, no config). Report the acceptance as
  blocked-on-harness, list what was verified from outside *with that
  caveat*, and never present an outside run as the acceptance.
- **Blocked-on-money**: acceptance moves real funds (wallet-mainnet).
  The draft parks as `ADAPT-draft.md` until a human is at the desk for
  the run; the site keeps saying "not installable yet" meanwhile.

## Conventions the runs converged on

Each of these earned its place by breaking a fresh agent at least
once. New ADAPTs start with all of them.

- **Durable venv, outside the git tree.** Bare `pip install` fails on
  PEP-668 systems; a venv inside the checkout dies with the checkout.
  Instruct `python3 -m venv <durable-path>` explicitly and say why.
- **Absolute paths in every snippet.** Agent harnesses reset cwd
  between commands; `pip install ./component` and relative state
  paths both break. This includes the pip target and the venv
  activate line.
- **Name every console script the install provides.** An agent that
  doesn't know `gpod-approve` exists can't tell the human to run it.
- **`{{BIN}}` placeholder convention.** The integration records the
  venv bin path once and substitutes it into every emitted command;
  bare names resolved through PATH are the most common integration
  failure in this catalog's history (Hermes rebuilds PATH from the
  login profile).
- **`actor: human` vs `approval: human`, spelled out.** actor-human
  steps are not the agent's to perform, but each carries a `verify:`
  — check first, ask only when the check fails. approval-human steps
  the agent DOES run; exit 4 on first attempt is the prompt to ask
  the human for a token, then retry once.
- **Generic-shell-tool degradation is acceptable, and says so.** If
  the harness has no per-tool gating, the walls still hold in the
  component's own code (walls-in-code); the integration loses only
  the harness-side extra belt and must say so in its report.
- **Exit-code taxonomy is the protocol.** List the recipe's full exit
  vocabulary in ADAPT and forbid collapsing codes; a wall refusal
  (exit 5) is a limit working, never an obstacle to retry around.
- **Acceptance probes declare their modality.** Which probes are
  config-N/A under some ratified walls (say so rather than forcing
  them), and which must run mid-cycle while live state exists (e.g. a
  max-pods refusal needs a live pod to refuse against).
- **Guardrail text is copied, not paraphrased.** Reflowing whitespace
  is fine; rewording is not.

## Checklist for shipping a new recipe's ADAPT

1. Draft `ADAPT-draft.md` from the five-step skeleton and the
   conventions above; read the manifest's `decide:`/`parameters:`
   blocks while writing step 2, not after.
2. Run fresh-agent acceptance (clean context, recipe + one sentence,
   zero help). Money-moving recipes wait for a human at the desk.
3. Apply every stumble as a doc fix; file real code/manifest bugs as
   beads. Re-run if the fixes were structural.
4. Check in `integrations/<harness>/<recipe>/` as produced by the run.
5. Rename to `ADAPT.md` — the site links it and drops the
   "not installable yet" label on the next publish.
6. Record the run (date, harness+version, pass/blocked, stumble
   count) in the shipping bead.
