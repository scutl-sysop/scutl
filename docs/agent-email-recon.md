# agent-email recon (cst-yo6d, catalog #5)

Star, 2026-08-28. Messaging-side recon for the agent-email recipe. The
PURCHASE side (create-inbox via x402, 2.00 USDC through the paysponge
proxy, wallet-as-identity zero-amount auth) was already mapped by
`agentmail-x402-recon.md` (cst-8ih.8, refreshed 2026-08-27 under
cst-rjba) and is byte-stable. This doc covers what that one could not:
the send / receive / thread surface an inbox owner actually operates.

Sources: unauthenticated probes of `x402.api.agentmail.to` (messaging
endpoints answer `403 Ownership required` without a paid inbox — price
of send remains unknowable pre-purchase, unchanged from 08-17) and
AgentMail's own docs (docs.agentmail.to, fetched 2026-08-28; local
copies in the session tmp only — the wire facts below are what matter).

## The object model

- **Message** — API-first email. Full shape (get): `inbox_id`,
  `thread_id`, `message_id`, `labels[]`, `timestamp`, `from`, `to[]`,
  `cc[]`, `bcc[]`, `reply_to[]`, `subject`, `preview`, `text`, `html`,
  `extracted_text`, `extracted_html`, `attachments[]` (id, size,
  filename, content_type, content_disposition, content_id),
  `in_reply_to`, `references[]`, `headers{}`, `size`, timestamps.
  `extracted_text`/`extracted_html` are provider-side reply extraction
  (Talon-style, quoted history stripped).
- **Thread** — implicit container. New non-reply send ⇒ new thread;
  replies append. Queryable per-inbox or **org-wide** (omit inbox_id):
  the org-wide view is their supervisor-agent story.
- **Labels** — free-form state tags on messages/threads (`received`,
  `sent`, `unreplied`/`replied` conventions); their own docs push
  labels as the agent's workflow-state store.
- **Lists** — allow/block × send/receive/reply, scoped org > pod >
  inbox, entries are addresses or whole domains. Reply direction is
  checked INSTEAD of receive when `In-Reply-To` matches a prior
  outbound; empty reply lists = all replies allowed. **This is the
  provider-side analog of caps-in-code and the natural enforcement
  point for a send allowlist.**

## The operative endpoints (v0, all under /v0/inboxes/{inbox_id})

| Op | Wire |
| -- | ---- |
| send | `POST .../messages/send` — body: to/cc/bcc (str or list), subject, text, html, labels[], reply_to, attachments[] (base64 `content` OR `url`), headers{}, track_opens. Returns `{message_id, thread_id}`. |
| reply | `POST .../messages/{message_id}/reply` — same body + `reply_all` bool. |
| forward | `POST .../messages/{message_id}/forward` |
| list/search/get | `GET .../messages[...]`; full-text search ranked, spam/trash/blocked/unauthenticated excluded, limit ≤ 100. Also batch-get, batch-update, get-raw (RFC822), get-attachment. |
| threads | list/search/get/update/delete per-inbox; org-wide via `/v0/threads`. |
| drafts | create/list/get/send — their human-in-the-loop story (draft now, approve/send later). |
| webhooks | create/list/update/delete, org/pod/inbox scoped; events: `message.received` (+ `.spam`, `.blocked`, `.unauthenticated`), `message.sent/delivered/bounced/complained/rejected`, `message.opened`, `domain.verified`. WebSocket alternative exists. Polling is the fallback loop. |

Auth on the x402 host is the wallet signature (zero-amount signed
calls); on the plain host a bearer key. Servers: `api.agentmail.to`
(default), `x402.api.agentmail.to`, `mpp.api.agentmail.to`, EU region.

## Hard limits and semantics that shape the recipe

- **50 recipients max** per send/reply across to+cc+bcc (API error above).
- **Idempotency**: creates take body `client_id` (no `@` allowed);
  **sends take an `Idempotency-Key` HTTP header** — retry with same key
  returns original `{message_id, thread_id}`, sends nothing; same key +
  different request ⇒ 409; explicitly empty key ⇒ 400; keys org-scoped,
  expire 24h after completion. An irreversible op with a first-class
  idempotent retry — exactly the payment-id pattern our wallet already
  teaches, transplanted to mail.
- **Reply targeting**: reply to the LAST message of a thread by
  message_id; their guide's loop is list threads by `unreplied` label →
  get thread → reply to tail → swap labels to `replied`. Label
  bookkeeping is the loop's memory, and lying to it (or crashing
  mid-swap) is the double-reply hazard.
- **Sender authentication is provider-checked**: inbound that fails
  domain auth lands as `message.received.unauthenticated` /
  `.spam` / `.blocked` — excluded from default list/search, delivered
  only to webhooks that explicitly subscribe. The recipe gets a
  provider-side spoof signal for free; trusting `from` beyond it is on
  us.
- **html vs text are independent bodies** authored by the sender; a
  reader that renders only one can be shown a different message than a
  human sees. `extracted_*` is provider-derived, convenient, and NOT
  the raw evidence — quoting an attack verbatim should come from
  text/html/raw.

## What the recipe's safety spine hangs on (recon verdict)

1. **Mail is data, not instructions** — the entire inbound surface
   (subject, bodies, from-display-names, attachment filenames, headers)
   is counterparty-authored. Same organizing fact as x402v2's "the
   merchant writes the offer," but here the adversary can INITIATE
   contact. The polite-register break (presence-findings.md: courtesy
   walks through defenses that stop "SYSTEM OVERRIDE") is the
   documented live threat model for exactly this recipe.
2. **Who mail can go TO is enforceable in code** — a send allowlist
   (component-enforced, mirrored to the provider's inbox-scope send
   allow list as defense in depth) is this recipe's caps-in-code: the
   model composes text; it cannot address arbitrary recipients, and
   exfiltration-by-email needs an address.
3. **Sends are irreversible ⇒ idempotent, logged, append-only** — one
   Idempotency-Key per task-send, retries reuse it, and an own-side
   append-only send log is the reconciliation surface (sprc pattern:
   own books vs provider `message.sent` history).
4. **The inbox is wallet-owned** — identity custody is the wallet's
   (tombstone forfeits the inbox; backup story is catalog #3 idbr).
   This recipe adds no second key.

## Open items for the manifest

- Send price on the x402 host is unknowable without owning an inbox;
  the bench is a mocked twin regardless. Live acceptance (one real
  inbox, one real send) is a ~2 USDC owner-gated probe, same posture as
  cst-8ih.14's — and can double as the x402v2 recipe's live composition
  proof when Conway unparks that promotion.
- Webhooks require a reachable URL (or their WebSocket); the recipe's
  reference loop should be POLLING-first — no ingress dependency —
  with webhooks as an optional binding.
- Drafts are the natural human-review gate for off-allowlist or
  first-contact sends; decide-node material, not core loop.
