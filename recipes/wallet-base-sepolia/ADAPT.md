# ADAPT — make this recipe available in your harness

You are an agent integrating the **wallet-base-sepolia** recipe into a
harness (Hermes, OMP, Claude Code, Codex, or any agent stack that can
run shell commands and carry a system prompt). This document is
addressed to you, the integrating agent. The human who pointed you here
wants one outcome: after you finish, an agent session in their harness
can hold testnet USDC and buy x402-priced resources, with every safety
wall still standing.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

The recipe's safety model is **walls-in-code, not walls-in-prompt**:
spending caps, key custody, idempotent payments, and approval gates are
enforced by a typed CLI (`signer`, plus the `x402-buy` driver). Your
harness integration cannot weaken them and does not need to reimplement
them. Your job is only to:

1. install the component,
2. expose its commands as tools (or documented shell invocations),
3. carry the recipe's guardrail text into the agent's system context,
4. run the ceremony and acceptance checks.

You never need to touch key material, and no step below asks you to.
If any adaptation you're considering would move, log, or echo a key or
an approval token, stop — that is the one hard boundary
(`components.signer.invariants` in the manifest).

## Step 1 — Install the component

```bash
python3 -m venv .adapt-venv && . .adapt-venv/bin/activate   # or your
                              # harness's venv convention — bare
                              # `pip install` fails on PEP-668 systems
pip install ./signer          # from this directory; installs THREE
                              # console scripts: signer, signer-approve
                              # (the human's token minter), x402-buy
signer status
```

Record the venv path — every tool invocation you emit must reach these
scripts (absolute paths if the harness's PATH won't carry the venv).

Expected: **exit code 2** ("not-setup") with a JSON body. That exit
code is healthy before keygen; anything else (import error, exit 1,
missing command) is an installation failure — fix PATH/venv before
continuing. If your harness runs tools under a restricted PATH (systemd
units, containers), reference the CLI by **absolute path**; bare names
resolved through PATH are the most common integration failure in this
catalog's history.

State lives in `${SCUTL_STATE:-~/.scutl/wallet}` — key (0600),
caps, append-only spend log. One state dir per wallet identity; point
`SCUTL_STATE` elsewhere for a second, isolated wallet.

## Step 2 — Expose the tools

Map each entry in `components.signer.tools` (recipe.yaml) into your
harness's tool format. The `command` field is the exact invocation;
`{slots}` are filled at call time by the agent. Whatever your
harness's convention — MCP tool, skill function, documented shell
command — preserve three properties:

- **Names and arity**: expose `wallet_status`, `wallet_pay`,
  `wallet_buy`, `wallet_sign`, `wallet_admin` as listed. Don't merge
  them into one free-form "run signer" tool; the separation is what
  lets a harness policy gate `wallet_admin` differently. If your
  harness only offers a generic shell tool (no per-tool gating), that
  degradation is ACCEPTABLE — the caps and approval gates hold in the
  signer's own code either way; you lose only the harness-side extra
  belt. Say so in your report rather than inventing a wrapper.
- **wallet_admin stays human-gated**: every admin op requires an
  approval token minted by the human (`signer-approve <op>`), enforced
  in code. Your integration must not automate token minting; if your
  harness has a human-confirmation feature, wire admin ops through it.
- **Exit-code taxonomy is the protocol**: 0 success, 2 not-setup,
  4 approval-required (ask the human, retry once), 5 cap-exceeded
  (report the offer, never retry around), 6 transient (retry with the
  SAME payment id). Surface exit codes to the agent; don't collapse
  them into a generic error string.

## Step 3 — Carry the guardrails into system context

Copy `execute.guardrails` from recipe.yaml **verbatim** into the
system prompt / skill text of the agent that will drive the wallet,
along with the `execute.loop` description. That text is small on
purpose. Do not paraphrase the guardrails; they are graded wording.

## Step 4 — Ceremony (with the human)

Follow `setup:` in recipe.yaml, in order: keygen (human approval) →
backup (human act, agent verifies) → fund (faucet, with the manual
fallback). Two distinct markers, two behaviors:

- **`approval: human`** (keygen): YOU run the command. Expect exit 4
  on the first attempt — no token pre-exists; that exit is the prompt.
  Ask the human to run `signer-approve <op>` (with the same
  SCUTL_STATE), then retry once.
- **`actor: human`** (backup): not yours to perform at all — print
  the instructions and wait; you only run the verify command after.

The faucet's silent failure mode is real: a drip on the wrong network
is invisible; the fallback text handles it.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run every check in `verify:` (recipe.yaml) from **inside the target
harness** — the point is to prove the integration, not the CLI. If the
harness cannot hold a session yet (no model configured, no auth),
report the acceptance as **blocked-on-harness**, list what you verified
from outside with that caveat, and never present an outside-the-harness
run as the acceptance:

1. `wallet_status` shows the funded address, chain_id 84532
2. `wallet_sign` round-trip
3. one real x402 test purchase (settle txhash confirmed on-chain,
   spend log entry matches)
4. over-cap probe refused **in code** (exit 5)
5. restart probe: state survives a signer restart

Report results to the human as a checklist with the actual outputs.
Any check you skipped, say so plainly.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it to
the catalog (issue, or tell your human) with: the step, the exact
command, the exact error, your harness and version. The acceptance
standard for this document is that a fresh agent completes it without
outside help.
