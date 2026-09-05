# ADAPT — make this recipe available in your harness

You are an agent integrating the **provision-vultr** recipe into a
harness (Hermes, OMP, Claude Code, Codex, or any agent stack that can
run shell commands and carry a system prompt). This document is
addressed to you, the integrating agent. The outcome your human wants:
after you finish, an agent session in their harness can rent, operate,
and destroy Vultr instances — with the spend walls still standing.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

The safety model is **walls-in-code, not walls-in-prompt**: the plan
allowlist, region allowlist, hourly-price ceiling, and instance ceiling
are enforced inside the `prov` CLI before any API call leaves the box.
Card-funded spend means the wallet's caps do NOT apply here — these
limits are the only wall, which is why they are owner-ratified numbers,
not defaults. Your job:

1. install the component,
2. expose its commands as tools (or documented shell invocations),
3. carry the recipe's guardrail text into the agent's system context,
4. walk the setup with the human and run the acceptance checks.

The API key is the one hard boundary: it lives only in the state dir at
0600, loaded per call, never in tool output or arguments. `set-key`
consumes a *file*, never a command-line argument. If any adaptation
you're considering would put the key on a command line, in an env var
you log, or in your context — stop.

## Step 1 — Install the component

If you are reading this on scutl.org (no local checkout), get the
source first — the component installs from the repo, not the web:

```bash
git clone https://github.com/scutl-sysop/scutl
cd scutl/recipes/provision-vultr
```

Then:

```bash
python3 -m venv <durable-path>/prov-venv && . <durable-path>/prov-venv/bin/activate
                              # pick a location OUTSIDE this git tree
                              # that survives past this session — the
                              # harness will reference it long-term;
                              # bare `pip install` fails on PEP-668
                              # systems
pip install ./prov            # from this directory; installs the
                              # console scripts: prov and prov-approve
                              # (the human's approval-token minter)
prov status
```

Record the venv bin path and substitute it into every command your
integration emits (a pinned placeholder like `{{PROV_BIN}}` replaced
at install time is the convention). Every tool invocation must reach these
scripts (absolute paths if the harness's PATH won't carry the venv;
bare names resolved through PATH are the most common integration
failure in this catalog's history).

Expected: **exit code 2** ("not-configured") with a JSON body. That
exit is healthy before configure; anything else is an installation
failure. State lives in `${SCUTL_PROV_STATE:-~/.scutl/provision}`.

## Step 2 — Expose the tools

Map each entry in `components.prov.tools` (recipe.yaml) into your
harness's tool format; `{slots}` are filled at call time. Preserve:

- **Names and arity**: `infra_status`, `infra_create`, `infra_list`,
  `infra_destroy`, `infra_destroy_all` (the emergency form — same
  ungated rule), `infra_dns`, `infra_admin`, as listed. Slot
  vocabulary (legal plans/regions, optional flags) lives in the
  manifest's `decide:`/`parameters:` blocks — read those before
  mapping, not after. If your
  harness only offers a generic shell tool (no per-tool gating), that
  degradation is ACCEPTABLE — the walls hold in `prov`'s own code;
  you lose only the harness-side extra belt. Say so in your report.
- **infra_destroy stays ungated**: destroy is deliberately the one op
  with no approval token and no decommission check — the safe
  direction is always open. Do not "helpfully" wrap it in a
  confirmation your harness enforces; a billing emergency is the
  wrong time for a ceremony.
- **infra_admin stays human-gated**: configure / set-key /
  decommission each require a token minted by the human
  (`prov-approve <op>`), enforced in code. Never automate token
  minting.
- **Exit-code taxonomy is the protocol**: 0 success · 2
  not-configured · 3 decommissioned (create/dns refuse; destroy still
  works) · 4 approval-required (ask the human, retry once) · 5
  limit-refused (report verbatim; do NOT retry with different
  parameters — a refusal is a limit working, not an obstacle) · 1
  invalid. Surface exit codes to the agent; don't collapse them.

## Step 3 — Carry the guardrails into system context

Copy the `execute.loop` text and the report-verbatim rule from
`smol.action.notes` (recipe.yaml) into the system prompt / skill text
of the agent that will drive provisioning. Do not paraphrase the
sentences; the YAML folded-scalar line breaks are formatting, not
content — reflowing whitespace is fine, rewording is not. The other
`smol:` rules are written for the smol lowering but are safe to carry
into any harness. The two
sentences that matter most: an instance nobody is using is spend, not
capacity — destroy when done; and never destroy what the log does not
know (foreign instances are reported, never touched).

## Step 4 — Setup (with the human)

Follow `setup:` in recipe.yaml, in order. Two distinct markers:

- **`actor: human`** (account, api-key, backup of nothing — read the
  list): not yours to perform — but each carries a `verify:` command,
  so CHECK FIRST and only ask the human when the check fails
  (verify-then-proceed). E.g. if `prov status` already reports
  `key_present true`, don't ask them to create an account.
- **`approval: human`** (configure): YOU run the command with the
  owner-ratified numbers. Expect exit 4 on the first attempt — no
  token pre-exists; that exit is the prompt. Ask the human to run
  `prov-approve configure` (same state dir), then retry once.

The limits are the human's decision. Present the manifest's questions
(allowlisted plans, regions, max instances, max hourly USD, optional
DNS subzone); never invent numbers, and never suggest raising a limit
as the fix for a refusal.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run the `verify:` checks from **inside the target harness** — the
point is to prove the integration, not the CLI. If the harness cannot
hold a session yet (no model configured, no auth), report the
acceptance as **blocked-on-harness**, list what you verified from
outside with that caveat, and never present an outside run as the
acceptance. Note that create/destroy cost real money (cents at
minutes-granularity billing) and need the human's go-ahead:

1. `infra_status` reports limits, key present, zero unreconciled
2. full cycle: `infra_create` (allowlisted plan) → poll `infra_list`
   to active+IP → `infra_destroy` → `infra_list` shows it gone
3. over-limit probe: a create outside the allowlist refuses **in
   code** with exit 5, no API call made
4. restart probe: state and instance log survive a process restart

Report results as a checklist with actual outputs, ids and IPs copied
character-for-character. Any check you skipped, say so plainly.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it
with: the step, the exact command, the exact error, your harness and
version. The acceptance standard for this document is that a fresh
agent completes it without outside help.
