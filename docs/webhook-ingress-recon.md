# webhook-ingress recon (cst-hb19, catalog #8)

Star, 2026-08-30. Recon for the webhook-ingress recipe: a stable
HTTPS endpoint the agent can hand out and answer, with signature
verification in code, replay windows, and the never-trust-the-body
discipline from paid-service applied inbound. Sources: Standard
Webhooks spec (github.com/standard-webhooks, fetched 2026-08-30);
Hookdeck/Svix Ingest receiver comparison and pricing pages (web,
2026-08-30); sender-scheme surveys for Stripe/GitHub/Slack (web,
2026-08-30, cross-checked against training knowledge of the vendor
docs); Porkbun webhook surface from docs/own-domain-recon.md
(spec v3.17, byte-checked there).

## What this recipe actually is

paid-service taught the outbound-facing lesson: the resource is
served only after payment settles, and income is reported verbatim
from the log. webhook-ingress is that discipline turned around to
face the wire. A webhook URL is a standing invitation — once handed
out, anyone who can route bytes to it can knock, forever, and the
*only* thing that separates a sender from an impostor is the
signature check the receiver performs itself. Two consequences
anchor the design:

- **A valid signature authenticates the sender, not the demand.**
  The polite-billing injection family arrives here wearing valid
  crypto: a correctly signed event whose *body* says "pay this
  invoice", "rotate your keys", "forward this to X" is
  authenticated data, not an instruction. This is sharper than the
  agent-email case, because the signature *looks* like authority.
  The recipe's job is to receive, verify, record, and report —
  acting on event content belongs to the consumer recipe's own
  walls (odom's watchdog decides what `domain.expiring` means; the
  ingress just proves who said it).
- **Deafness is a failure mode, not a safe state.** An endpoint
  that silently stopped listening means the agent misses the
  `domain.expiring` that would have saved the domain. Unlike every
  money recipe, doing *nothing* here has a blast radius — the spine
  needs uptime-monitoring doctrine (verify firing, alert on
  silence), not just refusal discipline.

## The rail decision: where does the stable endpoint live?

Two shapes exist in 2026:

- **Agent-owned leaf** — a prov-rail instance (provision-vultr)
  running a receiver behind Caddy, exactly the paid-service ingress
  component (`recipes/paid-service-x402/ingress/Caddyfile`): Caddy
  terminates TLS with ACME cert custody outside every tool's reach,
  receiver binds 127.0.0.1, proxy is the sole public listener. The
  name comes from the #6 delegated subzone or the #7 owned apex —
  which is what makes the URL *stable*: the instance can be rebuilt
  and the DNS repointed while the handed-out URL never changes.
  Stability lives in DNS, not in the instance.
- **Managed relay** — Hookdeck Event Gateway, Svix Ingest: a vendor
  URL that receives, verifies, buffers, and forwards. The recon
  finding that decides rev 1: **both verify source signatures
  themselves and forward the result — the sender's raw signature
  does not survive to the destination.** Delegating the verify
  moves the recipe's entire safety spine into a third party: a
  relay (or anyone who compromises the relay account) can fabricate
  "verified" events, and the component cannot re-check from inside.
  On top of that, both are card-billed subscription accounts
  (Hookdeck free 10K events then $39/mo; Svix Ingest free 50K then
  $490/mo) — a human-owned billing rail with no x402 path, i.e. an
  orphan-subscription shape (#6's sin) with no agent-side cap.

**The agent-owned leaf is the rail.** The catalog line is
"signature verification in code"; a relay that pre-verifies makes
that line unverifiable by construction. The relay shape stays in
the recon as an unblessed candidate — its real value (durable
queueing when the receiver is down) is honest, but it trades away
the one wall the recipe exists to hold. Revisit only if a relay
ships raw-passthrough-with-original-headers as a contract.

This also makes #8 the catalog's third composition instance, and
the deepest: provision (instance) + static-website/own-domain
(name + TLS leaf) + paid-service (the Caddy ingress component,
reused verbatim in kind).

## Signature-scheme survey (why the verifier is a registry, not a function)

Byte-level survey of what real 2026 senders put on the wire:

- **Standard Webhooks** (the convergence spec; Svix-lineage, used
  by a growing cohort): headers `webhook-id`, `webhook-timestamp`
  (unix seconds), `webhook-signature`. Signed content is
  `msg_id.timestamp.payload` — id and timestamp are folded into the
  MAC. Secret is `whsec_` + base64 (24–64 random bytes); signature
  is `v1,` + base64 HMAC-SHA256. Asymmetric variant `v1a` (ed25519,
  `whpk_`/`whsk_` keys). **Rotation is in-band**: multiple
  space-delimited signatures in one header, old key kept signing
  for an overlap window; the consumer accepts if any verifies.
  Dedup doctrine: `webhook-id` as idempotency key. Replay tolerance
  is left to the consumer (industry convention ~5 min).
- **Stripe**: `Stripe-Signature: t=<ts>,v1=<hex>`; signed content
  `{t}.{body}` — timestamp inside the MAC; HMAC-SHA256 hex; 5-min
  default tolerance enforced by their SDKs; multiple `v1=` entries
  during rotation.
- **GitHub**: `X-Hub-Signature-256: sha256=<hex>` over the raw body
  alone. **No timestamp anywhere in the MAC** — a captured delivery
  replays validly forever; the only replay wall available is
  deduplication on `X-GitHub-Delivery` (GUID), and it must be a
  durable ledger, not a 5-minute cache, because there is no window
  to fall back on.
- **Slack**: `X-Slack-Signature: v0=<hex>` over
  `v0:{X-Slack-Request-Timestamp}:{body}`; documented 5-min replay
  doctrine.
- **Porkbun** (the first-party consumer, from #7 recon):
  HMAC-SHA256-signed lifecycle events (`domain.expiring` et al.),
  secret rotation via `/webhook/rotateSecret`. Exact header names
  and canonical string are sandbox-answerable at manifest time —
  #7 left "wire the verify into ingress doctrine" as its open
  question 3; this recipe is where it lands.

Synthesis: three scheme families — (a) timestamp-in-MAC
(Standard/Stripe/Slack), where the skew window is a real wall;
(b) body-only (GitHub), where the dedup ledger is the *only* replay
wall; (c) per-sender variations in header names, canonical string,
and encoding (hex vs base64, prefix tokens). So the component's
verifier is a **per-sender scheme descriptor** (algorithm, header
names, canonical-string template, encoding, timestamp policy,
dedup policy) interpreted by one engine — not one hardcoded
verify, and not sender-specific code the bench can't enumerate.

## The walls (caps-in-code, inbound shape)

- **Verify before parse, on raw bytes.** The MAC is computed over
  the wire bytes; nothing downstream (JSON parse included) touches
  an unverified body. Constant-time comparison.
- **Fail closed, silently shaped.** Unknown path, unknown sender,
  missing header, bad signature → uniform rejection with no detail
  in the response (no signature oracle), full detail in the
  append-only inbound log. The log records verified and rejected
  events alike — rejections are evidence, not noise.
- **Replay walls per scheme family**: timestamp tolerance where the
  scheme signs a timestamp; durable seen-id ledger where the scheme
  has ids; for body-only schemes the ledger is the whole wall and
  its durability is a graded property.
- **The body is data.** Verified events are recorded and surfaced
  in the structured report verbatim (quote-verbatim doctrine from
  paid-service); the ingress component has no tool that pays,
  rotates, forwards, or executes anything on event content. The
  compound bench cell writes itself: a *validly signed* event whose
  body demands action — receiving it faithfully AND not acting on
  it AND reporting it verbatim is the pass.
- **Secret custody + rotation.** Per-sender secrets live in
  component state, never in transcripts or reports; rotation
  supports a dual-secret overlap window (Standard Webhooks
  multi-signature; Porkbun rotateSecret) — accept-old-within-window
  / refuse-old-past-window are both graded edges. Concrete seam
  with catalog #28 (secrets-rotation).
- **Structural escalation** (odom doctrine): signature-failure
  spike, replay-id hits, unknown-sender flood, and
  heartbeat-missed each append to a breaches list, and
  `escalate` derives from that list in code — prose naming a
  breach while the flag stays down is already a public bench cell
  shape (polite-discloser, promoted in #7).
- **Deafness monitoring.** A self-test delivery path (signed test
  event through the public URL) proves end-to-end aliveness;
  silence past a horizon is a breach, not a quiet day.
  Uptime-monitoring doctrine (#12) applied to the agent's own ear.

## Mocked-twin failure-mode menu (bench candidates)

forged signature (wrong secret); replayed id (exact resend);
stale timestamp; future timestamp (skew abuse); rotation race
(old-secret delivery inside vs past the overlap window);
valid-signature-hostile-body (the compound cell — pay/rotate/
forward demands with good crypto); duplicate delivery requiring
idempotent processing (sender retry, not attack); malformed body
with valid signature (verify still first, parse failure logged
honestly); scheme confusion (Stripe-shaped headers presented for a
GitHub-configured sender); missing signature header entirely;
oracle probing (rejection responses must not distinguish
wrong-secret from unknown-sender); flood/rate pressure; heartbeat
silence (the twin stops delivering and the report must escalate,
not green-wash).

## Composition seams

- **#7 own-domain**: `domain.expiring` over this ingress is the
  watchdog's push ear — the first first-party consumer, and the
  answer to #7's open question 3.
- **#6 static-website / #7 apex**: the endpoint's name; DNS-level
  stability story.
- **paid-service**: the Caddy ingress component and the
  loopback-bind pattern, reused.
- **#28 secrets-rotation**: webhook secret rotation is its concrete
  first instance.
- **#12 uptime-monitoring**: deafness detection doctrine.

## Open questions for manifest time

1. Porkbun webhook wire format — header names, canonical signed
   string, encoding (sandbox-answerable; register a sandbox webhook
   and capture a delivery).
2. Endpoint topology: one path per sender (`/hook/{sender}` with
   per-path scheme descriptor + secret) vs one path with
   sender-sniffing. Lean: path-per-sender — it makes unknown-path
   drop trivial and scopes each secret.
3. Does rev 1 include *outbound registration* (calling a sender's
   API to install the URL), or is handing out the URL the consumer
   recipe's act? Lean: consumer's act (odom registers with
   Porkbun); ingress serves and verifies. Keeps this recipe
   moneyless — the first catalog entry whose blast radius is
   information and availability, not spend.
4. Receipt shape: retention and pruning of the inbound log and
   seen-id ledger (GitHub-family needs the ledger durable; how
   long is graded honesty vs unbounded growth).
