# ADAPT — make this recipe available in your harness

You are an agent integrating the **paid-service-x402** recipe into a
harness (Hermes, OMP, Claude Code, Codex, or any agent stack that can
run shell commands and carry a system prompt). This document is
addressed to you, the integrating agent. The outcome your human wants:
after you finish, an agent session in their harness can operate a
service that SELLS for x402 payments — start/stop it, watch its
health, and report income truthfully.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

This is the money-IN recipe, and its safety model is
**walls-in-code, not walls-in-prompt**, aimed at two sins: serving
before payment, and lying about income. The `pserv` daemon refuses to
serve until the facilitator verifies AND settles (in code); `payTo` is
fixed in config and no request, argument, or model output can move it;
`earnings.log` is append-only and is the ONLY source income may be
quoted from. Your job:

1. install the component,
2. expose its commands as tools (or documented shell invocations),
3. carry the recipe's guardrail text into the agent's system context,
4. walk the setup with the human and run the acceptance checks.

Two components exist; only one is yours. `pserv` is the typed tool you
integrate. `caddy-ingress` (on the public-tls leaf) is HUMAN-provisioned
infrastructure — TLS keys belong to the caddy system user, outside
every tool's reach, and there are deliberately no agent-facing tools
for it. Do not create any.

## Step 1 — Install the component

If you are reading this on scutl.org (no local checkout), get the
source first — the component installs from the repo, not the web:

```bash
git clone https://github.com/scutl-sysop/scutl
cd scutl/recipes/paid-service-x402
```

Then:

```bash
python3 -m venv <durable-path>/pserv-venv && . <durable-path>/pserv-venv/bin/activate
                              # pick a location OUTSIDE this git tree
                              # that survives past this session — the
                              # harness will reference it long-term;
                              # bare `pip install` fails on PEP-668
                              # systems
pip install ./pserv           # from this directory; installs the
                              # console scripts: pserv and pserv-approve
                              # (the human's approval-token minter)
pserv status
```

Record the venv bin path and substitute it into every command your
integration emits (a pinned placeholder like `{{PSERV_BIN}}` replaced
at install time is the convention). Every tool invocation must reach these
scripts (absolute paths if the harness's PATH won't carry the venv;
bare names resolved through PATH are the most common integration
failure in this catalog's history).

Expected: **exit code 2** ("not-configured") with a JSON body — healthy
before configure (the other non-admin commands — start, stop,
earnings — also exit 2 pre-configure; that is the same healthy
signal, not a fault). State lives in
`${SCUTL_PSERV_STATE:-~/.scutl/paid-service}`.

## Step 2 — Expose the tools

Map each entry in `components.pserv.tools` (recipe.yaml); `{slots}`
are filled at call time — their legal values live in the manifest's
`decide:`/`parameters:` blocks (e.g. offering is `static-file` |
`generated-text`; `--resource-path` applies to static-file only;
default bind is 127.0.0.1:8402), so read those before mapping.
Preserve:

- **Names and arity**: `service_status`, `service_start`,
  `service_stop`, `service_earnings`, `service_admin`. Generic-shell
  harnesses (no per-tool gating): acceptable degradation — the walls
  hold in code; say so in your report.
- **service_admin stays human-gated**: configure / set-payto /
  decommission each require a token minted by the human
  (`pserv-approve <op>`), enforced in code. set-payto is the one that
  moves money's destination — never automate its token, and never
  fill its address from anything but the human's own typed input.
- **Exit-code taxonomy is the protocol**: 0 success · 2
  not-configured · 3 decommissioned (stop; tell the human) · 4
  approval-required (ask the human, retry once) · 1 invalid.
  Surface exit codes; don't collapse them.

## Step 3 — Carry the guardrails into system context

Copy `execute.loop` (recipe.yaml) into the system prompt / skill text
verbatim (folded-scalar line breaks are formatting; reflow whitespace,
never reword). Carry the `smol:` block's no-placeholder and
never-quote-remembered-state rules too — written for the smol
lowering, correct in any harness. The sentence that matters most: income is quoted ONLY from
`service_earnings`, with the total and settle tx copied
digit-for-digit into the report — an agent narrating remembered or
estimated income is this recipe's graded sin. Include also: a down
daemon that is not decommissioned gets restarted and re-verified;
a decommissioned one is left down and reported.

## Step 4 — Setup (with the human)

Follow `setup:` in recipe.yaml, in order. Marker semantics:

- **`approval: human`** (configure — fixes payTo, price, offering):
  YOU run the command with the human's values. Expect exit 4 on the
  first attempt — that exit is the prompt; ask the human to run
  `pserv-approve configure` (same state dir), then retry once. The
  payTo address must come from the human directly. Read it back to
  them before the approval.
- **`actor: human`** steps (ingress/Caddy on the public-tls leaf):
  not yours to perform — print instructions, wait, then run the
  step's `verify:` command. Check first where a verify exists; only
  ask when the check fails.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run the `verify:` checks from **inside the target harness**. "Cannot
hold a session" means concretely: no model configured/authenticated,
or no way to run the harness non-interactively — if you can open a
session that can call tools, that is the venue. Otherwise report
**blocked-on-harness** and
list what you verified from outside with that caveat. The paid-probe
check requires a buyer wallet (the wallet-base-sepolia or x402-v2
recipe) and the human's go-ahead:

1. `service_status`: daemon up, config summarized, payTo shown
2. unpaid request → 402 with a well-formed offer; resource NOT served
3. paid probe: a real x402 purchase round-trip — verify, settle
   txhash, resource served, `earnings.log` gains exactly one entry
4. replay probe: the same authorization again is refused before serve
5. restart probe: daemon restart; earnings totals re-derive unchanged

Report results as a checklist with actual outputs (totals and tx
hashes copied digit-for-digit). Any check you skipped, say so plainly.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it
with: the step, the exact command, the exact error, your harness and
version. The acceptance standard for this document is that a fresh
agent completes it without outside help.
