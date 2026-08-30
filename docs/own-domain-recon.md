# own-domain recon (cst-jgyy, catalog #7)

Star, 2026-08-30. Recon for the own-domain recipe: buy and hold a
domain the agent owns, on a card rail. Sources: Porkbun OpenAPI 3.0
spec v3.17 (api.porkbun.com/api/json/v3/spec, fetched 2026-08-30,
byte-checked field names) and its llms-full.txt reference; ICANN
transfer-policy pages and registrar lifecycle docs (icann.org,
dnsimple, namesilo, via web, 2026-08-30); registrar comparisons for
the rail decision.

## The rail decision (and why it is nearly forced)

Three candidates with real APIs:

- **Cloudflare Registrar** — at-cost pricing (.com ~$9.77) but the
  domain is welded to Cloudflare DNS, and programmatic *registration*
  is the API's weak corner. Cheapest, most lock-in.
- **Namecheap** — teaser first-year pricing with a renewal hike (the
  canonical dark pattern), an XML API gated behind IP whitelisting
  and a $50-balance/20-domain enablement threshold. The gate alone
  disqualifies it for a caps-first agent account.
- **Porkbun** — flat pricing (.com renewal ~$10.55), REST/JSON, and —
  decisive — an API built explicitly for agents (v3.17): sandbox
  mode, dry-run on every billable and destructive op, price-pinning
  on purchases, idempotency keys, signed webhooks, machine-readable
  `next_action.retryable` errors, and per-operation
  `x-porkbun-agent` safety metadata `{safe, cost, destructive,
  reversible, requiresConfirmation}`. There is even an official MCP;
  the recipe speaks the raw API (component owns its walls).

**Porkbun is the rail.** The dark-pattern decision tree the catalog
wanted still earns its keep — but notably *in-band*: the API
discloses its own teasers (below), so the tree runs on API fields,
not on scraping marketing pages.

## API surface (spec v3.17, byte-checked)

Base `https://api.porkbun.com/api/json/v3`; auth via
`X-API-Key`/`X-Secret-API-Key` headers or `apikey`/`secretapikey`
body fields; per-key IP and per-domain restrictions available.

- `POST /domain/checkDomain/{domain}` — availability + pricing:
  `avail`, `price`, **`firstYearPromo` (yes/no)**, `regularPrice`,
  **`premium` (yes/no)**, `minDuration`, and `additional.renewal` /
  `additional.transfer` price objects. The renewal price is quoted
  *before* purchase — the teaser pattern is disclosed by the rail.
- `POST /domain/create/{domain}` — requires **`cost` in pennies that
  must exactly equal the quoted total** (price-pinning: a quote/price
  race fails closed instead of charging the new price), plus
  `agreeToTerms`. `whoisPrivacy` optional (account default: enabled).
  Rate-limited 1 attempt/sec, 50 successes/day.
- `POST /domain/renew/{domain}` — same `cost` price-pinning.
- `dryRun: true` on create/renew/transfer runs every pre-flight
  (availability, price match, eligibility, **funds, spend limit**)
  and returns `wouldSucceed` + would-be cost without charging; also
  works on DNS writes and `updateNs` — destructive changes are
  rehearsable.
- `Idempotency-Key` header on writes; retries replay the original
  response for 24h — the double-charge failure mode is closed at the
  rail.
- `GET /domain/getRegistrationRequirements/{tld}` — JSON Schema for
  the create body per TLD, including whether the TLD is registerable
  via API at all. Validate before sending, not by failing.
- `GET /domain/get/{domain}` — `status`, `expireDate`,
  `securityLock`, `whoisPrivacy`, `autoRenew`, `apiAccess` (0/1
  ints). `POST /domain/updateAutoRenew/{domain}` flips auto-renew.
- `GET /account/balance` — prepaid credit in cents.
  `GET /account/apiSettings` — `monthlySpendLimit`, `monthlySpend`,
  `autoTopup`/`topupThreshold`/`topupAmount`, `lowBalanceAlert`.
- Webhooks (`/webhook/*`) — HMAC-SHA256-signed lifecycle events
  incl. `domain.registered`, `domain.renewed`, **`domain.expiring`**,
  `domain.transfer.completed`, `dns.record.*`.
- **Sandbox**: `pk1_sb_`/`sk1_sb_` keys, same base URL; simulated
  registrations against an isolated datastore with fake credit
  (`/sandbox/topup`, `/sandbox/reset`), *real* catalog pricing, and
  webhook delivery. Every response carries `"sandbox": true`.

## What the API does NOT give (the lock-in findings)

- **No transfer-out.** `authCode` exists only as *input* to
  `/domain/transfer` (transfer-IN). There is no endpoint to retrieve
  a domain's own EPP code or to unlock (`securityLock` has no update
  endpoint). Transfer-out is a web-UI human ceremony — the recipe
  must document it as the export path, not pretend to automate it.
- **ICANN 60-day locks** (policy, not registrar): no transfer within
  60 days of registration, of a prior transfer, or of a registrant
  change. A freshly bought domain is unexportable for two months by
  design; the manifest should state this so nobody grades "can't
  transfer day 1" as a wall failure.
- **Redemption**: past the renewal grace (~30–45d) a lapsed domain
  enters redemption; restoring costs $150–$270+, and transfer is
  impossible until restored. Missing a renewal is a ~20x cost event.

## The design tension: static-website inverted

Static-website's orphan was a subscription that *bills forever and
holds the only copy*. Own-domain's orphan is the mirror image: an
asset that *lapses* if unattended — and everything hung off it (site,
email, reputation) dies with it, then costs 20x to resurrect. There,
the sin was forgetting to destroy; here, the sin is forgetting to
*keep*. So the spine is a renewal watchdog, not a teardown ceremony:

- expiry horizon checked against `expireDate` every session +
  `domain.expiring` webhook as the push path;
- auto-renew ON is *not* a wall — it silently fails on an empty
  prepaid balance. The wall is monitored expiry + funded balance +
  honest escalation (uptime-monitoring doctrine: no green-washing).

Prepaid balance with `autoTopup` OFF is the caps-in-code ally: the
balance is the blast radius, exactly the buyer-wallet shape. Provider
`monthlySpendLimit` is defense-in-depth, not the primary cap.

## Decision tree (the catalog's ask), in API fields

Buy only if: `avail=yes` ∧ `premium=no` ∧ TLD on allowlist ∧
`additional.renewal.regularPrice` ≤ ceiling (price the *commitment*
at renewal price, never the teaser — `firstYearPromo=yes` is
information, not a discount to chase) ∧ dry-run `wouldSucceed=true`.
Then create with pinned `cost`, idempotency key, privacy on.

## Composition seam with #6

Porkbun carries its own DNS API (`/dns/*`, DNSSEC, glue), so the
owned apex can either host records on this rail or `updateNs` to
delegate at the registry — graduating static-website's delegated
subzone to an apex the agent actually owns. Two-rail composition,
second instance; `updateNs` is dry-runnable, which makes the
delegation rehearsable before it is real.

## Open questions for manifest time (sandbox-answerable)

1. Default `autoRenew` state on an API-created domain (terms mention
   automatic renewal — verify the flag, don't trust the prose).
2. Whether `/pricing/get`'s TLD catalog suffices for the allowlist
   ceiling check or per-domain `checkDomain` is always required.
3. Webhook signature verification details (secret rotation exists —
   `/webhook/rotateSecret`; wire the verify into ingress doctrine).
