---
name: wallet-base-sepolia
description: Operate the SCUTL agent wallet (Base Sepolia testnet USDC, x402 purchases). Use when a task needs to check wallet status, sign a message, pay an x402 offer, or buy an x402-priced resource. Spending caps and key custody are enforced in code by the signer CLI; this skill only teaches the invocations.
---

# Agent wallet — Base Sepolia (SCUTL recipe `wallet` rev 1)

The wallet is driven by a typed CLI. **Walls are in code, not in this
prompt**: spend caps, key custody, idempotent payments, and admin
approval gates are enforced by the `signer` / `x402-buy` binaries. You
cannot and must not try to work around them.

State dir: `${SCUTL_STATE:-~/.scutl/wallet}`. Set `SCUTL_STATE` to use
an alternate wallet identity.

Binaries live in the recipe venv — always invoke by **absolute path**
(bare names through PATH are the most common failure mode):

- `SIGNER=<VENV>/bin/signer`
- `X402_BUY=<VENV>/bin/x402-buy`

(The install step records the real `<VENV>` below; see INSTALL.md.)

## Tools (each is a distinct operation — never merge them)

| Tool | Invocation | Notes |
|---|---|---|
| `wallet_status` | `signer status` | address, network, balance, caps, spend today |
| `wallet_pay` | `signer pay --payment-id {payment_id} --to {to} --amount {usdc}` | direct x402 payment; REFUSES over-cap in code |
| `wallet_buy` | `x402-buy {url} --payment-id {payment_id}` | whole x402 purchase loop (offer → cap-check → pay → confirm → record) |
| `wallet_sign` | `signer sign {message}` | message signing |
| `wallet_admin` | `signer admin {op}` | keygen / backup-verify / revoke — HUMAN-GATED, see below |

`wallet_admin` ops:

- keygen: `signer admin keygen --cap-per-tx {cap_per_tx} --cap-daily {cap_daily}`
- backup-verify: `signer admin backup-verify`
- revoke: `signer admin revoke`

**wallet_admin stays human-gated.** Every admin op requires an approval
token the human mints out of band with `signer-approve <op>`. Never run
`signer-approve` yourself, never automate token minting, never place a
token in tool arguments or logs. On exit 4, ask the human to mint the
token, then retry the admin command once.

## Exit-code taxonomy (this IS the protocol — always check `$?`)

- `0` success
- `2` not-setup — run the setup ceremony (INSTALL.md) before use
- `4` approval-required — ask the human to run `signer-approve <op>`, retry once
- `5` cap-exceeded — report the offer to the human; NEVER retry around it
- `6` transient — retry with the SAME payment id

## Purchase loop

On a 402 response: call wallet_buy with the resource URL and a fresh
payment id (derive it from the task, e.g. task-slug-1). It reads the
offer, cap-checks, pays, confirms the settle tx on-chain, and records
the spend in one call.

- over-cap: exit 5 (cap-exceeded); report the offer and ask the human.
- transient: exit 6; retry with the SAME payment id (idempotent, never double-pays).

## Guardrails (verbatim from recipe.yaml — graded wording, do not paraphrase)

- One payment per offer; retries must reuse the same payment id (idempotent).
- Never place key material or approval tokens in tool arguments or logs.
