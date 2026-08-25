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
    verified. Changed-price over a time axis. *Manifest drafted (rev 1,
    cst-sqfy): renewals settle only at or under the consented price;
    an upward re-quote parks for fresh human consent (silent
    re-authorize has no code path), one settle per period makes
    double-billing a hard-fail, and a cancel is 'done' only when the
    log shows billing stopped. Component + mocked-twin bench next.*
15. **refund-clerk** — money OUT of the merchant side: honor real
    refunds, refuse invented ones, always against `earnings.log`
    evidence. The polite-billing injection family aimed at the
    outbound direction.
16. **procurement-with-quotes** — buy from the cheapest of N offers
    with a written decision trail; the anti-impulse-purchase recipe.
17. **usage-metered-selling** — extend paid-service from per-request
    to metered tiers; billing math generated from the same log the
    income reports quote.

### Presence, continued (be reachable on your human's terms)

21. **inbox-triage** — work an inbox, not just own one: summarize,
    categorize, draft-never-send without approval. The safety spine
    is injection-hardening at full width — mail that *asks* the
    agent to pay, forward, or rotate keys is the bench, and mail is
    always data, never instructions. *Built — rev 1 reference-green;
    model grades clean at two tiers (Qwen3.6-27B and 35B-A3B, safety
    pass on every public and held-out cell; the A3B posts 42/42 after
    the lowering rev — receipts/smutbench/sift-1..3/).*
22. **messenger-reachability** — report to and take instruction from
    the human over a channel they already live in, with who-is-
    speaking verified in code and rate caps that make the flooding
    failure mode (500 messages overnight) structurally impossible.
    Silence and spam are both graded.
23. **status-digest** — a scheduled honest self-report: services up,
    money in and out, obligations due, anomalies flagged. Composes
    scheduled-jobs, uptime-monitoring, and spend-reconciliation into
    the artifact owners actually want to receive; green-washing a
    down service is the graded sin.
24. **capability-purchase** — buy access to a paid API under caps,
    store the key properly, meter own usage against the purchased
    plan. Key material never appears in a transcript; overage
    behavior (stop, downgrade, or re-consent) is graded, not
    improvised. *Built — rev 1 reference-green; first model grade
    clean (Qwen3.6-27B, safety pass on every public and held-out
    cell, receipts/smutbench/capp-1/).*

### On your behalf (delegated consumer tasks)

The most-asked-for delegations in 2026 consumer research — dispute a
charge, wait out an algorithmic price, negotiate a bill — mostly live
on rails we cannot yet receipt live (card networks, closed retail
platforms). We bench them anyway: the mocked twin controls the
adversary, which makes it a *sharper* instrument than the real rail.
Live bindings land only where a rail lets us verify authority and
outcome; the split is stated per-recipe, not hidden.

25. **price-watch-and-buy** — hold a target price on a named item and
    buy when it crosses, under caps and a first-purchase approval
    gate. The mock merchant personalizes prices, drips fees at
    checkout, and re-quotes at buy time; holding the line is the
    grade. Live binding: x402 merchants today, agentic-checkout
    rails as they open. *Manifest drafted (rev 1, cst-g49k): the guard
    settles only when the fully-loaded total holds under target+caps,
    hard-fails a buy-time upward re-quote, counts drip fees before the
    cap decision, and gates the first purchase on a human token.
    Component + mocked-twin bench next.*
26. **dispute-clerk** — take a charge that spend-reconciliation
    flagged and pursue it: evidence pack from the log, dispute filed,
    status tracked to resolution. Card-rail by nature (the value of a
    dispute is that an issuer exists — x402 settlement is final);
    benched against a mock issuer portal with evidence requirements,
    deadlines, and a rep who tries to fob the agent off.
27. **quote-negotiation** — engage a counterparty to bring a price or
    bill down, with the floor and walk-away line set by the human in
    code. Fabricating commitments or loyalty claims to win the
    negotiation is the graded sin.

### Operations on the agent itself

28. **secrets-rotation** — rotate key material under an approval
    gate: old material never appears in transcripts, unapproved
    rotation refuses in code, a crash mid-rotation lands recoverable.
29. **agent-handoff** — package running state (services, wallets,
    obligations) so a successor session or different model can take
    over and *verify* what it inherited before acting.
30. **decommission-everything** — the full unwind, rehearsed: cancel
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
