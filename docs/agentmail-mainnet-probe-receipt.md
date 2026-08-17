# AgentMail mainnet probe — receipt (cst-8ih.14 step 6)

Date: 2026-08-17. Operator: Star (seat) + owner live in session (funding,
paid commands run from owner's terminal per money-moving precedent).

## Wallet

- Dedicated Base-mainnet buyer, host `SCUTL_STATE=~/.scutl/mainnet-buyer`
- Address: `0x3e59F6B7F14F641a04FF25507aF809d86f3E161C`
- Pinned network `eip155:8453` (rev-2 signer, one key one chain)
- Caps: 2.00 USDC/tx, 5.00 USDC/day. Funded 5.01 USDC by owner.

## Probe results (all x402 v2, via paysponge proxy)

1. **Create inbox** — POST /v0/inboxes, 2.00 USDC.
   Settle tx `0xe286e8dbb76ea6fdbda518208ba5920ed214d7eef907a290fdc10cfdc5f0a991`,
   block 50101488, independently verified via eth_getTransactionReceipt:
   status 0x1, USDC Transfer 2.0 from buyer to
   `0x6e3184C204e596dED89E8A5693B602097F4Ab687`.
   Result: **scutl-star@agentmail.to** owned by the buyer wallet.
2. **List inboxes / read messages** — amount "0", signed header as
   identity, no funds move. Worked repeatedly via `x402-buy --max 0`.
3. **Send** — POST .../messages/send, 0.01 USDC.
   Settle tx `0x23dde5c6c95a69def4ce952d1f2f4d286c3a7edc0b1fb908db21ba6599be2206`,
   confirmed. Self-send appears as one message labeled `sent` only — no
   looped-back received copy, so self-send is NOT arrival proof.
4. **Inbound delivery** — owner emailed the inbox from gmail; message
   arrived ~13s later, labels `received, unread`, SPF/DKIM/DMARC pass.

Final balance 3.00 USDC; daily cap usage 2.01/5.00. No retries needed —
every settle succeeded first attempt (unlike the Sepolia flake, which
validated the same-payment-id retry path earlier).

## Revocation / backup note (wallet-key-is-account)

The buyer key IS the AgentMail account: inbox ownership follows the
signature, there is no recovery path through the merchant. Therefore:

- **Revoke = lose the inbox.** Tombstoning this wallet abandons
  scutl-star@agentmail.to and any mail in it. Revoke is for compromise,
  not routine rotation.
- **Backup is mandatory, unlike disposable testnet buyers.**
  `backup_verified` is still **false**: owner must copy
  `~/.scutl/mainnet-buyer/keystore.json` + `kek` off-box, then run
  `signer admin backup-verify` (after `signer-approve backup-verify`).
- The recipe lowering must carry this asymmetry: testnet buyer keys are
  cattle, mainnet identity keys are pets.
