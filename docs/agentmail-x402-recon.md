# AgentMail x402 recon (cst-8ih.8)

Star, 2026-08-17. First-pass recon of AgentMail as the registry's first
EXTERNAL composition edge. No spend was made — everything below comes from
unauthenticated probes of `x402.api.agentmail.to` (a bare request returns
the full 402 offer) plus their docs. Captured offer:
`agentmail-402-offer-inboxes-create.json`.

## What their side of the wire looks like

- **Protocol: x402 version 2** (`"x402Version": 2`). Our wallet rev 1
  speaks v1. Schema differences that bite our parser:
  - amount field is `amount`, not `maxAmountRequired`
  - networks are CAIP-2 ids (`eip155:8453`), not names (`base-sepolia`)
  - `resource` object, `extensions.bazaar` (machine-readable input schema
    for the endpoint — method, bodyType, JSON body schema; genuinely nice)
- **Fronted by a proxy**: `resource.url` points at `api.paysponge.com`, so
  the actual counterparty for payment is paysponge, reselling AgentMail.
  One more party in the trust chain than the bead assumed.
- **Networks: mainnet only.** Offers list Base (8453), Solana, Avalanche
  (43114), X Layer (196), Polygon (137). **No testnet rail at all** — the
  "testnet recon" half of the bead scope is empty by construction.
- **Price points**: inbox create (POST /v0/inboxes) = **2.00 USDC**.
  List endpoints (GET /v0/inboxes, /v0/domains) = **amount "0"** — free,
  but still demand a *signed* X-PAYMENT header: the wallet signature IS
  the identity/auth. Send-message price unknowable without owning an
  inbox (returns 403 "Ownership required" first).
- **Wallet = account.** Resources are owned by the paying wallet
  (403 "This wallet does not own the requested resource" on foreign
  inboxes). Long-lived identity key, not a disposable payment key —
  changes our revocation story (tombstone the wallet = lose the inbox).

## Does wallet rev 1 x402-buy handle it unmodified?

**No — five independent blockers:**

1. `buy.py` hard-rejects any offer whose `network != "base-sepolia"`;
   theirs is `eip155:8453`.
2. v2 field names (`amount`) KeyError against our v1 parser
   (`maxAmountRequired`); `accepts[0]` blind-pick also needs to become
   select-by-network (five offers in the array).
3. GET-only driver: inbox creation is POST with a JSON body; x402-buy
   has no method/body support.
4. Chain binding is compile-time sepolia: `CHAIN_ID = 84532`, sepolia
   RPCs, and the EIP-712 domain hardcodes `{"name": "USDC"}` — Base
   mainnet USDC's domain name is `"USD Coin"` (their `extra` says so).
   Signature would not verify even if everything else were patched.
5. Zero-amount signed calls (auth-only requests) have no path through
   Signer.authorize/spend-recording; caps and payment_id semantics
   assume every call is a purchase.

## What a real edge would need (rough shape, not a commitment)

- A **mainnet execution profile** for the wallet recipe: network as
  recipe binding (chain id, RPC, USDC contract + EIP-712 domain from the
  offer's `extra`), real-money caps as the sole governance surface.
  This is the big one — it's a policy decision (first real-USDC rail),
  not a code patch.
- x402 **v2 offer parsing** + offer selection by blessed network.
- POST/body support in x402-buy (the bazaar extension hands us the body
  schema — a lowering could consume it).
- An identity story for wallet-owned resources (the buyer key becomes a
  durable account credential; backup/rotation interacts with the
  backup/restore-identity catalog candidate).
- "Verify" against a counterparty whose books we can't read: settle tx
  on-chain is still checkable (our merchant-settles confirm pattern
  should transplant), plus a functional check — does the inbox exist /
  does mail arrive.

## Verdict for the bead

The composition edge is real and well-shaped (agent-native, inbox as
primitive, machine-readable schemas), but it is **mainnet-first, 2 USDC
smallest-real-spend, via a proxy counterparty**. Smallest honest next
step if we proceed: fund a dedicated buyer key with ~$5 USDC on Base
mainnet (owner action), patch the five blockers as a v2/mainnet spike —
NOT a published recipe rev — and run one create-inbox + one send as the
acceptance probe. Whether recipe #4 is this edge or a life-skills recipe
stays an owner call.
