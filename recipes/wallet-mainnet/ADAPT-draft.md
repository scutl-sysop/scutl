# ADAPT — make this recipe available in your harness

> DRAFT status (cst-q03b): this file ships as ADAPT-draft.md and is not
> yet linked from the catalog. It becomes ADAPT.md — and the recipe
> becomes installable — only after a fresh-agent acceptance run proves
> it, per the catalog's ADAPT pattern. The acceptance for THIS recipe
> moves real money, so that run happens with the human at the desk.

You are an agent integrating the **wallet-mainnet** recipe into a
harness (Hermes, OMP, Claude Code, Codex, or any agent stack that can
run shell commands and carry a system prompt). This document is
addressed to you, the integrating agent. The human who pointed you here
wants one outcome: after you finish, an agent session in their harness
can hold real USDC on Base mainnet and buy x402-priced resources, with
every safety wall still standing.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

**Read this before anything else:** mainnet is unforgiving. There is no
faucet, no reset, and no transaction that can be taken back. The recipe
is built so that every irreversible step is either code-gated,
human-performed, or preceded by a micro-probe that proves the path
before real money follows it. Your integration must not shortcut any of
those three shapes. If you have not installed **wallet-base-sepolia**
before, consider recommending the human start there — it is the same
spine with a faucet and nothing at stake.

## What you are installing (30 seconds of orientation)

The safety model is **walls-in-code, not walls-in-prompt**: spend caps
(per-tx, daily, lifetime), the ceremony gate, the ratchet cooling-off
delay, panic, and key custody are enforced by a typed CLI (`msigner`,
plus the `x402-buy` driver). Your harness integration cannot weaken
them and does not need to reimplement them. Your job is only to:

1. install the component,
2. expose its commands as tools (or documented shell invocations),
3. carry the recipe's guardrail text into the agent's system context,
4. run the ceremony and acceptance checks with the human.

You never need to touch key material, and no step below asks you to.
If any adaptation you're considering would move, log, or echo a key or
an approval token, stop — that is the one hard boundary
(`components.msigner.invariants` in the manifest).

## Step 1 — Install the component

`msigner` depends on `scutl-signer`, the sibling wallet-base-sepolia
package (not on PyPI). Install both, in order:

```bash
python3 -m venv <durable-path>/msigner-venv && . <durable-path>/msigner-venv/bin/activate
                              # pick a location OUTSIDE this git tree
                              # that survives past this session; bare
                              # `pip install` fails on PEP-668 systems
pip install ../wallet-base-sepolia/signer   # dependency: scutl-signer
                                            # (also provides x402-buy)
pip install ./msigner         # console scripts: msigner,
                              # msigner-approve (the human's token
                              # minter — not for agent use)
msigner status
```

Record the venv path — every tool invocation you emit must reach these
scripts (absolute paths if the harness's PATH won't carry the venv;
bare names resolved through PATH are the most common integration
failure in this catalog's history).

Expected: **exit code 2** ("not-setup") with a JSON body. That exit
code is healthy before keygen; anything else is an installation
failure — fix PATH/venv before continuing.

State lives in `${SCUTL_MWALLET_STATE:-~/.scutl/mwallet}` — encrypted
key (0600), caps, ratchet queue, append-only spend log, ceremony
record, panic marker. One state dir per wallet identity. **`x402-buy`
selects this wallet through `SCUTL_STATE`** — export
`SCUTL_STATE=~/.scutl/mwallet` (or your chosen dir) in the environment
of every `x402-buy` invocation, or it will drive the wrong (or no)
wallet. Keep the mainnet state dir distinct from any testnet wallet's.

## Step 2 — Expose the tools

Map each entry in `components.msigner.tools` (recipe.yaml) into your
harness's tool format. The `command` field is the exact invocation;
`{slots}` are filled at call time by the agent. Whatever your
harness's convention, preserve four properties:

- **Names and arity**: expose `mw_status`, `mw_pay`, `mw_buy`,
  `mw_sign`, `mw_panic`, `mw_admin` as listed. Don't merge them into
  one free-form "run msigner" tool; the separation is what lets a
  harness policy gate `mw_admin` differently. If your harness only
  offers a generic shell tool, that degradation is ACCEPTABLE — the
  caps, ceremony gate, and approval gates hold in the signer's own
  code either way. Say so in your report rather than inventing a
  wrapper.
- **mw_admin stays human-gated**: every admin op (keygen,
  backup-verify, restore-rehearsal, ratchet, unpanic, revoke, sweep)
  requires an approval token minted by the human (`msigner-approve
  <op>`, same state dir), enforced in code. Ratchet and sweep tokens
  carry SCOPE — the number, the destination — typed by the human at
  approval time. Your integration must not automate token minting.
- **mw_panic must stay one word away**: panic is deliberately NOT
  approval-gated — stopping is always safe, and an incident is the
  wrong time for a token ceremony. Do not bury it behind
  confirmations, wrappers, or harness-side approval; it must be
  callable by bare model intent from any state.
- **Exit-code taxonomy is the protocol**: 0 success, 2 not-setup,
  3 tombstoned (revoked; all ops refuse), 4 approval-required (ask the
  human, retry once), 5 cap-exceeded (report the offer, never retry
  around), 6 transient (retry with the SAME payment id), 7
  ceremony-incomplete (the missing steps are human ceremony, not
  something to work around), 8 permanent, 9 panicked. Surface exit
  codes to the agent; don't collapse them into a generic error string.
  NOTE: this table diverges from the inner testnet signer (which uses
  7 for permanent); callers of `msigner` follow THIS table.

## Step 3 — Carry the guardrails into system context

Copy `execute.guardrails` from recipe.yaml **verbatim** into the
system prompt / skill text of the agent that will drive the wallet,
along with the `execute.loop` description. Do not paraphrase the
guardrails; they are graded wording. Two of them deserve your special
attention as the integrator, because harness design can defeat them:

- "Pay only the offer's payTo" — do not build any convenience that
  fills a destination from conversation context.
- "mw_panic FIRST, report second" — make sure the agent can actually
  reach the panic tool without an approval prompt in the way.

## Step 4 — Ceremony (with the human)

Follow `setup:` in recipe.yaml, **in order — the order is
load-bearing**: nothing real is at risk until funding, and by then the
restore path is PROVEN, not hoped. Three distinct markers:

- **`approval: human`** (keygen, restore-rehearsal): YOU run the
  command. Expect exit 4 on the first attempt — no token pre-exists;
  that exit is the prompt. Ask the human to run `msigner-approve <op>`
  (same state dir), then retry once.
- **`actor: human`** (backup, micro-fund): not yours to perform at
  all — print the instructions and wait; you only run the verify
  command after. For backup that means TWO offline copies (the wallet
  IS the account; losing it is losing everything it owns). For
  micro-fund it means the human sends a micro amount (e.g. 0.50 USDC)
  from their own exchange/wallet FIRST, you confirm the exact amount
  on-chain via `mw_status` (poll every 60s, at most 30 times), and
  only then does the real amount follow.
- **micro-probe**: one real x402 payment at micro scale — the first
  irreversible outbound act, sized so being wrong is cheap. Confirm
  the settle txhash and the spend log entry before the human sends the
  real balance.

No ETH is ever needed: the wallet is **gasless by construction**
(EIP-3009 — every spend is a transfer authorization the counterparty
submits and pays gas for; the signer never broadcasts a transaction).
The wrong-network warning is real: a deposit on the wrong chain or in
the wrong token is invisible here and possibly unrecoverable.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run every check in `verify:` (recipe.yaml) from **inside the target
harness** — the point is to prove the integration, not the CLI. If the
harness cannot hold a session yet, report the acceptance as
**blocked-on-harness**, list what you verified from outside with that
caveat, and never present an outside-the-harness run as the acceptance.

Real-money notes, from the recipe's first live install (2026-09-04,
receipts/mwallet/1):

1. Ceremony-gate probe: spend tools refuse (exit 7) until keygen +
   backup-verify + restore-rehearsal are ALL recorded — check this
   BEFORE any funding.
2. Restore rehearsal passes with the address unchanged, while the
   balance is still zero.
3. Micro-probe: one live x402 purchase, settle txhash confirmed,
   spend log entry matches.
4. Over-cap probe: an offer above cap_per_tx refused in code (exit 5)
   before any signature.
5. Lifetime probe: the lifetime cap binds even with per-tx/daily
   headroom.
6. Ratchet probe: run with `ratchet_delay_hours=0.05` (3 minutes) so
   acceptance never waits a day — the delay is a parameter precisely
   for this; RESTORE the real delay (default 24h) immediately after,
   and have the human re-ratify whatever caps stand. A pending raise
   must be visible in mw_status and inert until maturity; a lower
   applies immediately; a token-less ratchet attempt fails.
7. Panic probe: mw_panic freezes spend and admin instantly with no
   token; status stays readable; unpanic requires a human token.
8. Restart probe: kill the signer, restart; address, caps, pending
   ratchet, and counters derive unchanged from the append-only log.

Report results to the human as a checklist with the actual outputs.
Any check you skipped, say so plainly — and for this recipe, sweep
rehearsal may be deliberately deferred by the human (it moves the
whole balance); record that choice rather than silently omitting it.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it to
the catalog (issue, or tell your human) with: the step, the exact
command, the exact error, your harness and version. The acceptance
standard for this document is that a fresh agent completes it without
outside help — and without ever improvising around a refused spend.
