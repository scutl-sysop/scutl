# ADAPT — make this recipe available in your harness

You are an agent integrating the **gpu-rental** recipe into a harness
(Hermes, OMP, Claude Code, Codex, or any agent stack that can run
shell commands and carry a system prompt). This document is addressed
to you, the integrating agent. The outcome your human wants: after
you finish, an agent session in their harness can rent, operate, and
destroy RunPod GPU pods — with the spend walls still standing and no
pod ever left billing silently.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

The safety model is **walls-in-code, not walls-in-prompt**: the
GPU-type allowlist, hourly-price ceiling, pod-count ceiling, and
region pin are enforced inside the `gpod` CLI before any API call
leaves the box. GPU rental bills by the hour at roughly ten times the
rate of ordinary instances — the failure this recipe exists to
prevent is the forgotten pod, so destroy is deliberately the one
always-open door, and a destroy whose verification fails exits
**UNDEAD (exit 6)** with billing-may-be-accruing language you must
surface, never retry silently. Your job:

1. install the component,
2. expose its commands as tools (or documented shell invocations),
3. carry the recipe's guardrail text into the agent's system context,
4. walk the setup with the human and run the acceptance checks.

The API key is the one hard boundary: it lives only in the state dir
at 0600, loaded per call, never in tool output or arguments.
`set-key` consumes a *file*, never a command-line argument. If any
adaptation you're considering would put the key on a command line, in
an env var you log, or in your context — stop.

## Step 1 — Install the component

If you are reading this on scutl.org (no local checkout), get the
source first — the component installs from the repo, not the web:

```bash
git clone https://github.com/scutl-sysop/scutl
cd scutl/recipes/gpu-rental
```

Then:

```bash
python3 -m venv <durable-path>/gpod-venv && . <durable-path>/gpod-venv/bin/activate
                              # pick a location OUTSIDE this git tree
                              # that survives past this session — the
                              # harness will reference it long-term;
                              # bare `pip install` fails on PEP-668
                              # systems
pip install /abs/path/to/scutl/recipes/gpu-rental/gpod
                              # absolute path on purpose: agent
                              # harnesses often reset cwd between
                              # commands, and the venv lives outside
                              # the tree. Installs the console
                              # scripts: gpod and gpod-approve (the
                              # human's approval-token minter)
gpod status
```

Record the venv bin path and substitute it into every command your
integration emits (a pinned placeholder like `{{GPOD_BIN}}` replaced
at install time is the convention). Every tool invocation must reach
these scripts (absolute paths if the harness's PATH won't carry the
venv; bare names resolved through PATH are the most common
integration failure in this catalog's history — Hermes in particular
rebuilds PATH from the login profile, so bind into `~/.local/bin` or
use absolute paths).

Expected: **exit code 2** ("not-configured") with a JSON body. That
exit is healthy before configure; anything else is an installation
failure. State lives in `${SCUTL_GPOD_STATE:-~/.scutl/gpu-rental}`.

## Step 2 — Expose the tools

Map each entry in `components.gpod.tools` (recipe.yaml) into your
harness's tool format; `{slots}` are filled at call time. Preserve:

- **Names and arity**: `gpu_status`, `gpu_create`, `gpu_list`,
  `gpu_destroy`, `gpu_destroy_all` (the emergency form — same
  ungated rule), `gpu_stock` (read-only availability), `gpu_admin`,
  as listed. Slot vocabulary (legal GPU types, optional image) lives
  in the manifest's `decide:`/`parameters:` blocks — read those
  before mapping, not after. If your harness only offers a generic
  shell tool (no per-tool gating), that degradation is ACCEPTABLE —
  the walls hold in `gpod`'s own code; you lose only the harness-side
  extra belt. Say so in your report.
- **gpu_destroy stays ungated**: destroy is deliberately the one op
  with no approval token and no decommission check — the safe
  direction is always open. Do not "helpfully" wrap it in a
  confirmation your harness enforces; a billing emergency is the
  wrong time for a ceremony.
- **UNDEAD is an emergency, not a retry**: a destroy that cannot
  verify the pod gone exits 6 naming the pod id, with billing
  language. Surface it to the human verbatim, immediately. Never
  swallow it, never loop on it quietly.
- **gpu_admin stays human-gated**: configure / set-key / decommission
  each require a token minted by the human (`gpod-approve <op>`,
  same state dir), enforced in code. Never automate token minting.
- **Exit-code taxonomy is the protocol**: 0 success · 2
  not-configured · 3 decommissioned (create refuses; destroy still
  works) · 4 approval-required (ask the human, retry once) · 5
  wall-refused (report verbatim; do NOT retry with different
  parameters — a refusal is a limit working, not an obstacle) · 6
  UNDEAD (see above) · 1 invalid. Surface exit codes to the agent;
  don't collapse them.

## Step 3 — Carry the guardrails into system context

Copy the `execute.loop` text and both `execute.guardrails` entries
(recipe.yaml) into the system prompt / skill text of the agent that
will drive rentals. Do not paraphrase the sentences; the YAML
folded-scalar line breaks are formatting, not content — reflowing
whitespace is fine, rewording is not. The three sentences that matter
most: a pod nobody is computing on is spend, not capacity — destroy
when done; an UNDEAD destroy is surfaced verbatim, never retried
silently; and never destroy what the log does not know (foreign pods
are reported, never touched).

## Step 4 — Setup (with the human)

Follow `setup:` in recipe.yaml, in order. Two distinct markers:

- **`actor: human`** (account, API key): not yours to perform — but
  each carries a `verify:` command, so CHECK FIRST and only ask the
  human when the check fails (verify-then-proceed). E.g. if
  `gpod status` already reports `key_present true`, don't ask them
  to create an account.
- **`approval: human`** (configure): YOU run the command with the
  owner-ratified numbers. Expect exit 4 on the first attempt — no
  token pre-exists; that exit is the prompt. Ask the human to run
  `gpod-approve configure` (same state dir), then retry once.

The walls are the human's decision. Present the manifest's questions
(GPU-type allowlist, max hourly USD, max pods, region pin, optional
attach-only network volume — whose monthly cost then appears in every
status report); never invent numbers, and never suggest raising a
limit as the fix for a refusal.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run the `verify:` checks from **inside the target harness** — the
point is to prove the integration, not the CLI. If the harness cannot
hold a session yet (no model configured, no auth), report the
acceptance as **blocked-on-harness**, list what you verified from
outside with that caveat, and never present an outside run as the
acceptance. Note that create/destroy cost real money (cents at
minutes-granularity billing, but the hourly rate is high — do not
idle) and need the human's go-ahead:

1. `gpu_status` reports the walls, key present, the volume named
   with its monthly cost, zero unreconciled
2. full cycle: `gpu_stock` → `gpu_create` (allowlisted type) → poll
   `gpu_list` to running → `gpu_destroy` → verified-gone line;
   `rentals.log` shows open + verified-closed. "Running" in the
   list JSON is `desiredStatus: RUNNING` plus a populated
   `publicIp`/`portMappings` — there is no bare `status` field.
   Run the max_pods probe (below) while this cycle's pod is still
   up; it needs a live pod to refuse against.
3. over-limit probes: a create outside the allowlist, above the
   price ceiling, or beyond max_pods each refuse **in code** with
   exit 5 (`wall-refused` — the manifest's name for limit-refused),
   no API call made. A probe the ratified walls make unreachable
   (e.g. no allowlisted type prices above the ceiling) is N/A —
   say so rather than forcing it
4. restart probe: state and rentals log survive a process restart

Report results as a checklist with actual outputs, pod ids copied
character-for-character. Any check you skipped, say so plainly. If
your cycle ends in UNDEAD, the acceptance has failed — escalate to
the human with the exact output and do not report success.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it
with: the step, the exact command, the exact error, your harness and
version. The acceptance standard for this document is that a fresh
agent completes it without outside help.
