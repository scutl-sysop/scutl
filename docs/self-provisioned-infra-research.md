# Self-provisioned infrastructure — phase-1 research (cst-8ih.11)

*Star, 2026-08-15. Owner's question (2026-08-14): "can my agent go spin
up its own VPS and pay for it and use it with minimum human
intervention?" Thesis correction on the bead applies throughout: this is
NOT an autonomy play — "human click + reliable" beats "fully auto +
unreliable provider." The research maps the reliability/autonomy
tradeoff honestly instead of chasing the fully-autonomous ideal.*

## Headline findings

1. **x402-native infrastructure does not exist yet.** The x402
   ecosystem (165M+ transactions by early 2026, Linux-Foundation
   x402 Foundation stewarding the spec) is APIs all the way down:
   data, scraping, inference-per-call. The awesome-x402 registry has
   **zero** entries for VPS rental, GPU rental, DNS, or domains. Our
   wallet's rail (USDC on Base, x402/EIP-3009) currently buys no
   substrate anywhere.

2. **Crypto-payable VPS exists, but on the wrong rail and the wrong
   trust tier.** The real market is the no-KYC/privacy segment:
   - **SporeStack** (2017, API-first, no account/email — funds live on
     a bearer token; XMR/BTC/BCH **+ USDT-ERC20**, the only stablecoin
     sighting). Philosophically the closest to agent-payable: token in
     env var, OpenAPI spec, launch/manage servers entirely by API.
     But: bearer-token custody is a new key-material class our signer
     doesn't model, USDT-ERC20 ≠ our USDC-on-Base wallet, and the
     privacy positioning means IP reputation / abuse-neighborhood risk
     for a public-TLS merchant.
   - **BitLaunch** (2017, reseller of DigitalOcean/Vultr/Linode
     capacity, hourly billing, Go/Python SDKs + CLI) — needs an email
     account (human ceremony, once), then BTC/LTC/ETH top-ups are
     API-drivable. Middle of the tradeoff curve: real datacenters
     underneath, crypto on top, but a reseller's continuity risk.
   - **0xNull** and similar newer entrants: no track record (<1 yr),
     exactly the "sketchy provider" the thesis correction warns about.
   - **Njalla** covers the DNS/domain leg (JSON-RPC API, API tokens,
     BTC/ETH/LTC, certbot plugin exists) — but Njalla legally owns the
     domain, and it's the privacy tier again. Cloudflare (our current
     DNS) has the best API and a human-owned account.

3. **The trusted tier is human-account-gated by design.** Vultr,
   Hetzner, DigitalOcean, RunPod (we operate on two of these today)
   all have excellent APIs behind an account a human creates with a
   card. KYC-ish ceremony is their fraud control; it will not
   disappear. On this tier the human intervention is **one-time**
   (account + payment method), and everything after — create, image,
   firewall, DNS record, destroy — is agent-drivable with a scoped API
   key. Our own pod-up.sh / POD-RUNBOOK work is already this pattern.

## The tradeoff, mapped

| Tier | Examples | Human ceremony | Rail | Reliability | Agent surface |
|------|----------|----------------|------|-------------|---------------|
| x402-native | (none exist) | none | our wallet | — | — |
| Bearer-token crypto | SporeStack | none (token custody instead) | XMR/BTC/USDT-ERC20 | medium, 2017 track record, privacy-segment neighbors | full API |
| Account + crypto top-up | BitLaunch | email once, top-up approvals | BTC/LTC/ETH | medium (reseller) | full API |
| Trusted account + card | Vultr/Hetzner/DO/RunPod + Cloudflare DNS | account + card once | fiat | high | full API via scoped key |

The governance insight: **our cap discipline only governs the wallet
rail.** On the trusted tier, spend governance moves to provider-side
quotas/limits and a scoped API key — a different enforcement surface,
not enforced by our code. A recipe that says "caps are the sole
governance surface" is only true on rails the signer pays; anything
card-funded needs its own invariants (key scope, instance-count/price
ceilings checked in a typed tool before create-calls).

## Recommended phase-2 shape (owner decision wanted)

**Target architecture: trusted provider + human at the account/payment
click + agent doing everything else** — per the thesis correction, as
the legitimate design, not a fallback. Concretely:

- Recipe candidate `provision`: typed tool wrapping ONE provider's API
  (Vultr is the natural first: we know it, snapshot/restore proven on
  the merchant box) with invariants in code: instance-type allowlist,
  max concurrent instances, max hourly price, mandatory destroy path,
  DNS record CRUD limited to a delegated subdomain zone.
- Human-actor steps: account existence, payment method, API-key
  provisioning — all rendered verify-then-proceed (the cst-8ih.9
  wording; this recipe is the inversion that bead predicted: rev-2's
  HUMAN checkpoints become agent actions with a verify command).
- Acceptance demo stays as filed: agent bootstraps the rev-2 merchant
  on infrastructure it procured itself; the buyer is the wallet
  recipe. Ladder cost is dominated by instance-hours, small.
- **Optional experimental leaf, not the trunk:** a SporeStack
  bearer-token leaf would test the "money in wallet, go do stuff"
  boundary honestly — but it needs a custody story for the token
  (signer-style state dir, never in transcripts) and an owner ruling
  on whether the privacy-tier neighborhood is acceptable for anything
  reachable from town infrastructure. Priced in XMR/BTC it also does
  not exercise our wallet; it would be procurement-by-config, not
  procurement-by-x402.

## What would change the picture

x402-native compute is the thing to watch: if a real provider fronts
instance-hours with 402 offers, the wallet recipe composes directly
and caps become real governance for substrate. Nothing sighted yet;
worth a re-scan at each rev.

## Sources

- https://sporestack.com/ · https://kycnot.me/service/sporestack
- https://bitlaunch.io/ · https://hostscore.net/review/bitlaunch/
- https://kycnot.me/service/0xnull
- https://njal.la (API: https://njal.la/api/1/, JSON-RPC) ·
  https://github.com/chaptergy/certbot-dns-njalla
- https://github.com/xpaysh/awesome-x402 (no infra entries as of
  2026-08-15)
- x402 ecosystem state: https://www.allium.so/blog/x402-explained-the-internet-native-payments-standard-for-apis-data-and-agent-commerce/ ·
  https://zuplo.com/blog/mcp-api-payments-with-x402
