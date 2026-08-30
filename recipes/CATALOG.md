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
   tombstone). *Manifest drafted (rev 1, cst-4c6b): ceremony order is
   load-bearing — keygen, backup, restore rehearsal all pass BEFORE the
   first cent arrives; cap raises are approval-gated and take effect
   only after a cooling-off delay (lowers immediate); panic is the one
   un-gated op; micro-probe before real money on every new path,
   including sweep.*
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
   else on this page. *Manifest drafted (rev 1, cst-r0vz): the
   reconciler moves no money and holds no credentials — reads books,
   chain, and prov-fetched billing, so the whole attack surface is
   lying in the report; every residue is explained by a named honest
   category or escalated with the joined evidence verbatim (no fudge
   line, tokened re-baseline only); chain transfers are discovered
   via getLogs, not asserted, so an unlogged transfer out — the
   drain — is a first-class finding, never a balance mystery.
   Component (sprecon, 28 tests) and mocked-twin bench
   (smutbench/sprc: 20 public cells zero skips, 5 broken-policy
   sins, held-out round sprc1-2026-08) landed; graded GREEN on
   Qwen 3.6 27B, public + heldout all axes 1.0 (ladder/sprc/).*

### Presence (be reachable, be readable)

5. **agent-email** — a real inbox the agent owns and pays for
   (AgentMail-shaped: x402-purchased, wallet-owned). Send, receive,
   thread; injection-hardened reading is the safety spine — mail is
   data, not instructions. *Manifest drafted (rev 1, cst-yo6d): the
   sender writes the message and can initiate contact, so the walls
   are code — a recipient allowlist is this recipe's caps-in-code
   (exfiltration needs an address), every send is idempotency-keyed
   and append-only logged, reconcile against provider history closes
   each session; inbound demands are refused AND quoted whatever
   their register — the polite ask is the same attack as the barked
   override. Recon: docs/agent-email-recon.md.*
6. **static-website** — publish a site: object storage + DNS + TLS on
   the provision rail. The agent's public face, and the first
   composition test (provision + DNS recipes as dependencies).
   *Manifest drafted (rev 1, cst-8bm9): the adversarial surface is the
   agent's own failure modes with money and exposure — an
   object-storage subscription bills monthly until deleted AND holds
   the site's only copy, so teardown is an export-verify ceremony;
   tier price is checked in code before any create; public ACLs scope
   to the declared site root only; a publish is 'serving', not
   'uploaded' — every file live-fetched and hash-matched before the
   claim; two blessed serving leaves (provider-domain bucket URL with
   native HTTPS, or a prov-rail instance terminating TLS for one name
   in the delegated subzone — the catalog's first two-rail
   composition). Component (scutl_sweb) and mocked-twin bench
   (smutbench/sweb, held-out round sweb1-2026-08) landed; graded
   GREEN on Qwen 3.6 27B, public + heldout all axes 1.0
   (ladder/sweb/, cst-8bm9). Recon: docs/static-website-recon.md.*
7. **own-domain** — buy and hold a domain on a card rail; renewals,
   lock-in, transfer-out. Registrars are the sharpest dark-pattern
   territory we know of; the decision tree earns its keep here.
   *Manifest drafted (rev 1, cst-jgyy): static-website inverted — the
   orphan doesn't bill forever, it LAPSES, and the identity hung off
   it dies into a ~20x redemption cliff, so the spine is a renewal
   watchdog with honest escalation (a breach sets the flag, not just
   the prose — disclosure is not alarm), not a teardown ceremony.
   The rail (Porkbun) discloses its own dark patterns in-band, so
   the decision tree runs on API fields: commitment priced at
   renewal regularPrice never the teaser, premium refused, TLD
   allowlist, dryRun rehearsal, pinned-cost buys with idempotency
   keys. Prepaid balance is the blast radius (autoTopup found ON is
   a wall breach); transfer-out is reported as the dated human
   ceremony it is — no EPP/unlock API exists. Composition seam:
   od_delegate graduates sweb's delegated subzone to an owned apex.
   Component (scutl_odom, 40 tests) and mocked-twin bench
   (smutbench/odom: 20 public cells zero skips, 5 broken policies
   incl. polite-discloser — the under-escalation sin isolated on its
   own axis — held-out round odom1-2026-08); graded GREEN on
   Qwen 3.6 27B, public + heldout all axes 1.0 (ladder/odom/). The
   heldout compound cell caught a real component bug live: a
   crash-retry's dry-run rehearsal falsely refused a charge-free
   idempotent replay against the already-debited balance.
   Recon: docs/own-domain-recon.md.*
8. **webhook-ingress** — a stable HTTPS endpoint the agent can hand
   out and answer; signature verification in code, replay windows,
   the "never trust the body" discipline from paid-service applied
   inbound. *Manifest drafted (rev 1, cst-hb19): the catalog's first
   moneyless entry — the blast radius is information and
   availability, not spend. The endpoint is agent-owned end-to-end
   (prov-rail instance behind the paid-service Caddy ingress, name
   in the sweb subzone or odom apex — URL stability lives in DNS);
   managed relays are deferred by finding, not omission: the 2026
   relays verify source signatures themselves and forward, which
   un-codes the recipe's spine. The verifier is a per-sender scheme
   descriptor interpreted by one engine (three incompatible wire
   families in the wild; for body-only schemes like GitHub's the
   durable dedup ledger is the ONLY replay wall). Design center: a
   valid signature authenticates the sender, not the demand — no
   tool pays, rotates, or forwards on event content — and deafness
   is a failure mode: a signed heartbeat through the public URL
   proves the ear, silence escalates structurally. Seams: Porkbun
   domain.expiring is the first consumer (#7's open question 3
   lands here); #28 gets its first concrete instance in secret
   rotation. Component (scutl_wing, 31 tests — wall-ordering
   finding: the dedup ledger outranks the skew wall, because an
   exact replay carries its original now-stale timestamp) and
   mocked-twin bench (smutbench/wing: 19 public cells zero skips,
   5 broken policies incl. blind-admitter — content-as-admission-
   authority isolated on the safety monitor — held-out round
   wing1-2026-08 with a replay-under-silence compound; the twin
   backs the public URL with a real loopback server, so the
   heartbeat cells prove the ear end-to-end) landed;
   reference-green public + heldout. Grade next.
   Recon: docs/webhook-ingress-recon.md.*

### Substrate (things every other recipe leans on)

9. **durable-object-storage** — put/get/verify with integrity checks
   and a restore rehearsal; the backup target for every stateful
   recipe. *Manifest drafted (rev 1, cst-px98): a backup is a claim
   until a restore proves it — the spine is the scheduled rehearsal
   (fetch to scratch, re-hash vs manifest, verbatim report), and the
   characteristic sin is green-washing. Verify wall is agent-side
   SHA-256 in an agent-owned manifest (2026 checksum fracture:
   multipart ETags aren't digests, SDK-default checksums 400 on
   Ceph-family backends) — provider metadata advisory, never the
   wall. Rail: Vultr Legacy on the prov-rail seam ($6/mo base,
   endpoint byte-checked as Ceph RGW tentacle), the only candidate
   adding zero new trust relationships; needs a SECOND
   object-storage-scoped API key (Conway act — the prov key
   IAM-403s here by finding, and should keep 403ing). Puts never
   overwrite, over-cap parks (never rotates old backups to fit),
   key material never rides (#3's human-copy boundary intact), and
   teardown isn't done until the endpoint probe fails.
   Recon: docs/durable-object-storage-recon.md.*
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
    log shows billing stopped. Component (substew, cst-zinq) and
    mocked-twin bench (cst-ydb0) landed: reference-green on the public
    menu and held-out round rn1; broken policies (gullible-renewer,
    hike-absorber, silent-keeper, misreporter) each fail exactly their
    axis. First model grade next (cst-wh29).*
15. **refund-clerk** — money OUT of the merchant side: honor real
    refunds, refuse invented ones, always against `earnings.log`
    evidence. The polite-billing injection family aimed at the
    outbound direction. *Manifest drafted (rev 1, cst-4kdg): a refund
    settles only against a settle earnings.log proves — once per
    settle, at most the settled amount, to the recorded payer (the
    payout address is never a parameter, so the redirect lure has no
    code path); unproven claims refuse with evidence, true-but-
    outside-policy claims park for a human exception, and honoring a
    real claim is graded alongside refusing a fake one. Component
    (refclerk, cst-oivh) landed: status/claim/verify/refund/admin,
    every check re-derived in code from the read-only earnings.log and
    append-only refunds.log; exception/deny tokens scoped to the claim
    id. Mocked-twin bench (cst-jydd) landed: reference-green on the
    public menu and held-out round rf1; broken policies (gullible-clerk,
    park-jumper, silent-clerk, misreporter) each fail exactly their
    axis. First model grade landed (cst-cia8,
    receipts/smutbench/refund-1/): Qwen3.6-35B-A3B, zero safety
    violations on every public and held-out cell — no phantom refund,
    no misdirected payout, no self-granted exception; injection and
    redirect residue is report wording only (paraphrase, not
    omission).*
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
