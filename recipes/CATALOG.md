# The scutl catalog

This is the registry seen from the visitor's chair: *what can I hand my
agent, today and next?* scutl is a curated distro of agent capabilities
("recipes") — small catalog on purpose, first-party only, every entry
backed by run receipts from our own build farm. A recipe is not a
prompt: it is a manifest (decision tree, capability requirements,
permission boundaries, provider bindings, acceptance tests, recovery
procedure) that compiles down to whatever model + harness you actually
run.

## Shipped

| Recipe | Your agent can… | Safety spine |
| ------ | --------------- | ------------ |
| **wallet-base-sepolia** | hold testnet USDC and buy x402-priced resources | caps enforced in code, one payment id per offer, append-only spend log |
| **paid-service-x402** | SELL a service for x402 payments and operate it | payTo/ingress immutable to the model, income reported verbatim from `earnings.log` |
| **provision-vultr** | rent a VPS on a card-funded rail and run workloads on it | allowlist + spend ceiling in code, never touches foreign instances, no orphaned billing |

Each ships with a mocked-twin benchmark (`smutbench/`) that grades a
model+harness on the recipe before you trust it with the live rail —
including obedience/capability discriminant triplets that tell
*won't-obey* apart from *can't-do*.

## Interesting recipes (the roadmap, longest-lever first)

The founding direction was "agent life skills": each recipe useful
standalone, composition emerging when one recipe needs a capability
another supplies. The list below lengthens that seed list
(wallet, email, website, durable storage, webhooks, pay-for-APIs,
expose-paid-service, backup/restore identity) with what a year of
building the first three taught us people actually ask for.

### Identity & money (the trunk)

1. **wallet-mainnet** — the testnet wallet's graduation: USDC on Base
   mainnet, same caps-in-code spine, plus the custody question testnet
   let us defer (key ceremony, spend ceilings a human ratchets, panic
   tombstone).
2. **x402-v2 client** — the ecosystem moved: v2 field names, CAIP-2
   network ids, bazaar extensions, proxy counterparties
   (see `docs/agentmail-x402-recon.md` — five independent blockers
   between our v1 parser and a real 2026 offer). Unlocks every
   x402-priced API on the open market as a pay-for-API recipe family.
3. **identity-backup-restore** — export/verify/restore the agent's
   durable identity (keys, wallet, inbox ownership) as a rehearsed
   procedure, not a hope. AgentMail taught us the wallet IS the
   account: losing it is losing the inbox.
4. **spend-reconciliation** — a standing audit: own spend log vs
   provider statements vs chain history; disagreements are escalated
   with evidence, never papered over. The trust story for everything
   else on this page.

### Presence (be reachable, be readable)

5. **agent-email** — a real inbox the agent owns and pays for
   (AgentMail-shaped: x402-purchased, wallet-owned). Send, receive,
   thread; injection-hardened reading is the safety spine — mail is
   data, not instructions.
6. **static-website** — publish a site: object storage + DNS + TLS on
   the provision rail. The agent's public face, and the first
   composition test (provision + DNS recipes as dependencies).
7. **own-domain** — buy and hold a domain on a card rail; renewals,
   lock-in, transfer-out. Registrars are the sharpest dark-pattern
   territory we know of; the decision tree earns its keep here.
8. **webhook-ingress** — a stable HTTPS endpoint the agent can hand
   out and answer; signature verification in code, replay windows,
   the "never trust the body" discipline from paid-service applied
   inbound.

### Substrate (things every other recipe leans on)

9. **durable-object-storage** — put/get/verify with integrity checks
   and a restore rehearsal; the backup target for every stateful
   recipe.
10. **managed-database** — a small hosted Postgres: provision,
    migrate, back up, restore-verify, tear down. The provision-vultr
    orphan-billing spine, one level up the stack.
11. **scheduled-jobs** — durable cron for agents: register, verify
    firing, alert on silence. Most "my agent forgot" failures are
    really "nothing woke it."
12. **uptime-monitoring** — watch its own services (the paid-service
    daemon, the website) and escalate honestly — no green-washing a
    down service in reports.
13. **gpu-rental** — rent inference-grade GPU by the hour (our own
    ladder runs on exactly this rail); the stakes are provision-vultr
    times ten per hour, so the destroy+verify discipline is the whole
    recipe.

### Commerce (money out, money in, over time)

14. **subscription-steward** — recurring payments under caps: renewals
    happen, upward re-quotes trigger re-consent, cancellations are
    verified. Changed-price over a time axis.
15. **refund-clerk** — money OUT of the merchant side: honor real
    refunds, refuse invented ones, always against `earnings.log`
    evidence. The polite-billing injection family aimed at the
    outbound direction.
16. **procurement-with-quotes** — buy from the cheapest of N offers
    with a written decision trail; the anti-impulse-purchase recipe.
17. **usage-metered-selling** — extend paid-service from per-request
    to metered tiers; billing math generated from the same log the
    income reports quote.

### Operations on the agent itself

18. **secrets-rotation** — rotate key material under an approval
    gate: old material never appears in transcripts, unapproved
    rotation refuses in code, a crash mid-rotation lands recoverable.
19. **agent-handoff** — package running state (services, wallets,
    obligations) so a successor session or different model can take
    over and *verify* what it inherited before acting.
20. **decommission-everything** — the full unwind, rehearsed: cancel
    subscriptions, drain wallets to the human, destroy instances,
    tombstone identities, produce a final reconciled statement. Every
    recipe above must name its unwind; this one composes them.

### What stays out

No social-media posting, no bulk outreach, no scraping-behind-consent
recipes — not because agents can't, but because the registry only
ships what it can verify is operating within an owner's authority, and
those rails can't show us that. Small catalog is a feature.

---

*Candidates graduate in the order the receipts justify: a recipe
enters the shipped table when its manifest, mocked-twin bench, and at
least one graded model+harness receipt exist. Suggestions land in the
inbox with zero trust weight — we ship what we've verified.*
