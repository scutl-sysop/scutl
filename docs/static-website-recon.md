# static-website recon (cst-8bm9, catalog #6)

Star, 2026-08-28. Recon for the static-website recipe: publish a site
the agent owns, on the provision rail. Sources: Vultr docs
(docs.vultr.com FAQ + upload-webpage guide, fetched 2026-08-28),
govultr client source (the API-shape ground truth; docs.vultr.com/api
403s non-browser fetches), and the existing scutl_prov component,
whose network layer already speaks the same API v2 and whose DNS ops
already govern a delegated subzone.

## The rails (two, and that is the design tension)

1. **Management plane** — Vultr API v2, same bearer key the provision
   recipe scoped. Object storage is a *subscription*: card-funded,
   priced per tier per month, billed until deleted. This is
   provision-vultr's orphan-billing problem one level up: an orphaned
   instance bills ~cents/hour; an orphaned storage subscription bills
   forever and *holds the site's only copy*, so destroy is no longer
   trivially safe — teardown ordering matters.
2. **Data plane** — S3-compatible endpoint issued per subscription
   (`s3_hostname`, e.g. `ewr1.vultrobjects.com`, plus
   `s3_access_key`/`s3_secret_key`). Bucket content is served publicly
   over HTTPS at `https://{bucket}.{cluster}.vultrobjects.com/{key}`
   once the object/bucket ACL is public.

## Management API surface (from govultr, byte-checked field names)

- `POST /v2/object-storage` — body `cluster_id`, `tier_id`, `label`;
  returns `id`, `region`, `status`, `s3_hostname`, `s3_access_key`,
  `s3_secret_key`. **The only time secrets arrive unprompted.**
- `GET /v2/object-storage` / `GET /v2/object-storage/{id}` — list/get
  (secrets included in get; treat every response as sensitive).
- `DELETE /v2/object-storage/{id}` — ends billing AND destroys content.
- `POST /v2/object-storage/{id}/regenerate-keys` — the rotation
  primitive; old S3 keys die, new pair returned.
- `GET /v2/object-storage/clusters` (+ `/clusters/{id}/tiers`),
  `GET /v2/object-storage/tiers` — tier has `price`, `bw_gb_price`,
  `disk_gb_price`, `slug`. Price ceiling check lives here, pre-create,
  exactly like prov's plan/hourly ceiling.
- `GET|POST /v2/object-storage/{id}/bucket`,
  `DELETE .../bucket/{name}` — control-plane bucket ops exist, but
  console cannot delete buckets >50k objects; data-plane delete is the
  reliable path.
- Tiers are NOT upgradeable in place (FAQ): migration = new
  subscription + copy + delete. A recipe rev-2 concern; rev 1 pins one
  tier at manifest time.

## What the data plane does and does not give a website

- **Gives:** public HTTPS on the provider domain, worldwide, zero
  servers. `s3cmd put -P` / `setacl --acl-public`, Content-Type must be
  set correctly (the guide sets it by hand — a publish tool must map
  extensions to MIME types itself).
- **Does not give:** website mode. No index document at `/`, no error
  document, no redirects — visitors get `/index.html` by its full key
  or nothing. No white-label custom domains: a CNAME
  (`www.example.com → bucket.ewr1.vultrobjects.com`) resolves, but TLS
  serves the `*.vultrobjects.com` certificate → browser hard-fails.
  **Custom-domain TLS on the bucket alone is impossible.** (FAQ states
  no white-label support; the CNAME workaround is HTTP-realistic only.)

## The TLS story, therefore (and the composition test the catalog wanted)

Two publish modes, both worth blessing:

- **provider-domain** — site lives at the bucket URL, native HTTPS,
  no instance, no DNS. Cheapest possible presence; ugly name; no
  index-at-root (tools emit explicit links, or the landing page IS the
  full URL handed out).
- **custom-subzone** — a provisioned instance (prov rail, existing
  plan/region allowlists) runs a TLS terminator (Caddy-shaped: ACME
  HTTP-01, auto-renew) with an A record in the delegated subzone
  (prov's existing `dns_set`, which already refuses names outside the
  subzone). The instance either reverse-proxies the bucket (bucket
  stays source of truth; index/error/redirect rules live in the proxy
  config) or serves a synced copy (bucket becomes the durable store +
  restore source). Either way: **this is the first recipe that
  composes two live rails** — object storage for durable content,
  provision for name+TLS — and the failure surface is the composition
  (instance dies → site down but content safe; bucket key rotates →
  proxy must re-auth; DNS record deleted → ACME renewal fails later,
  not now).

## Failure modes to seed the manifest/bench

Provider/rail:
1. Orphaned subscription — billing forever, invisible to wallet caps.
2. Create-retry double-subscription (409-less rail; list-before-retry).
3. Secret exposure — s3 keys arrive in API responses; must never reach
   transcript/logs; regenerate-keys is the rotation, and it strands any
   deployed proxy config until updated (rotation is a *procedure*).
4. Tier price drift / wrong tier — ceiling check pre-create.
5. Delete ordering — delete subscription with content = site+backup
   gone in one call; destroy wants a verified export first.

Content/publish:
6. Over-broad public ACL — publishing must scope to the site prefix;
   a bucket also used as durable store must not go world-readable
   wholesale.
7. Defacement / wrong-target put — writes outside the declared site
   root (bucket+prefix) refused in code.
8. Wrong/missing Content-Type — renders as download; publish tool owns
   the MIME map and verifies served headers.
9. Publish-verify gap — "uploaded" ≠ "serving": verify by fetching the
   public URL and hash-matching, per file, before reporting success.
10. Injection via site content — the site's own files are data; a page
    containing instructions must never steer the tool (mail-is-data
    discipline from #5/#21, applied to HTML).

Name/TLS (custom-subzone leaf):
11. DNS record outside the delegated subzone — already refused by prov;
    the site tool must route DNS through prov, never raw API.
12. ACME failure loop — rate limits from retry storms; cert renewal
    silently failing (grade: report cert expiry honestly).
13. Instance orphan/zombie — prov's own spine, inherited via
    composition, not reimplemented.

Reporting:
14. Green-washing — "site is up" claimed without a live fetch; uptime
    and cert facts quoted from checks, never asserted.

## Live-probe notes

None run this session — every fact above is docs/source-derived, and
the management surface is the same authenticated API v2 the prov
component already exercises daily. First live touch belongs to the
component's acceptance run under the scoped key, with tier price
checked against the ceiling before the create leaves the box.
