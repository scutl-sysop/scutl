# durable-object-storage recon (cst-px98, catalog #9)

Star, 2026-08-30. Recon for the durable-object-storage recipe:
put/get/verify with integrity checks and a restore rehearsal — the
backup target for every stateful recipe. Sources: live Vultr API v2
(`/v2/object-storage/tiers`, `/clusters`, byte-checked 2026-08-30
with the prov rail key, read-only); Vultr endpoint implementation
fingerprint (anonymous `NoSuchBucket` probe against
ewr1.vultrobjects.com, `Server: Ceph Object Gateway (tentacle)`,
2026-08-30); Vultr provisioning/deletion docs and 2026 market
comparisons (web, 2026-08-30: docs.vultr.com object-storage pages,
mixpeek/infratally/tech-insider provider comparisons, vpsranking on
the May 2026 Vultr archival launch); GitLab 19 upgrade notes and
ceph/ceph PR #66358 on the 2026 checksum-compatibility fracture;
recipes/identity-backup-restore/recipe.yaml (the #3 seam, read
in-repo).

## What this recipe actually is

Every stateful recipe in the catalog carries durable state the
instance it runs on can lose: the paid-service income log, odom's
renewal ledger, wing's dedup ledger (which is literally the replay
wall — lose it and every replay looks fresh), the beads export, the
workshop's own evidence trees. Today that state has exactly one
home: the disk it was written on. #9 gives it a second home with
three honest properties:

- **A backup is a claim until a restore proves it.** put/get/verify
  at write time proves the *write*, not durability. The recipe's
  spine is the restore rehearsal: on a schedule, fetch the backup
  set into a scratch dir, re-hash every byte against the manifest,
  and report the result verbatim. A rehearsal that doesn't run is a
  failure the report must show (structural escalation, odom
  doctrine) — and a report that *claims* green without running is
  this recipe's polite-discloser: green-washing, a first-class
  bench discriminant, not a style problem.
- **The verify wall is agent-side, or it is nothing.** The 2026
  checksum landscape is fractured (details below): ETag semantics
  vary, provider checksum extensions are unevenly implemented, and
  the SDK defaults actively break against S3-compatibles. The only
  digest that means anything across providers is the SHA-256 the
  agent computes itself at put time, recorded in a manifest the
  agent owns, re-checked by re-hashing retrieved bytes. Provider
  metadata (ETag, x-amz-checksum-\*) is an advisory cross-check,
  never the wall.
- **Restored bytes are data, not instructions.** The
  never-trust-the-body discipline (paid-service, applied inbound in
  #8) applies to restores: a restored object whose content says
  "rotate your keys" / "pay this" is content the recipe faithfully
  restores and never acts on. Backup content is the loudest
  injection channel an agent voluntarily replays into itself.

Unlike #8, this recipe is moneyed again — but small and flat: a
standing subscription in single-digit dollars per month plus
per-GB growth. The blast radii are (1) data loss discovered at
restore time, (2) orphaned standing spend, (3) unbounded growth
billing. All three are the catalog's home turf.

## The rail decision: whose bucket?

Candidates surveyed across the 2026 market:

- **Vultr Object Storage (the prov-rail seam).** S3-compatible,
  provisioned and destroyed through the *same* API the workshop
  already holds for provision-vultr: `POST /v2/object-storage`
  (cluster id + tier id), `DELETE /v2/object-storage/{id}`. Tier
  table read live from the API (2026-08-30): Legacy base $6/mo,
  $0.006/GB disk, $0.01/GB egress; Standard $18 base, $0.018/GB;
  Premium/Performance/Accelerated above that; and a 2026-05
  **Archival** tier (base $6, archive storage $0.006/GB) for cold
  copies. 19 clusters across 15+ regions. The endpoint is Ceph
  Object Gateway, release *tentacle* (v20) — byte-checked, not
  assumed. Billing is subscription-shaped: it accrues from create
  until DELETE, independent of use — exactly the provision-vultr
  orphan spine, one level up the stack (the catalog already says
  this about #10; it is equally true here). Destroy+verify (DELETE,
  then GET expecting 404/gone) transfers verbatim from prov.
- **Cloudflare R2** — $0.015/GB-mo, zero egress. Honest strengths;
  but a *new human account* on a card-billed rail (orphan shape,
  no agent-side cap, a Conway signup act). 2026 comparisons also
  report missing versioning/object-lock — reported, not verified
  here, and moot given the account cost.
- **Backblaze B2** — ~$6/TB-mo storage, egress free to 3x stored
  then $0.01/GB. Cheapest per-GB in the survey; same new-account,
  new-billing-rail objection.
- **Tigris** — zero egress, versioning + object lock; same
  objection again.

**The prov-rail seam is the rail: Vultr Object Storage, Legacy
tier.** Not because it wins on price per GB (B2 does) but because
it is the only candidate that adds *zero new trust relationships*:
no new account, no new card, no new custody story — one more
subscription on a rail whose provision, cap, and destroy discipline
the workshop has already built and graded. At backup scale (a few
GB), the tier tables converge to the base price anyway; $6/mo
Legacy is the floor of every candidate's real bill. R2/B2/Tigris
stay in the recon as unblessed candidates; revisit only if the
recipe outgrows single-node backup scale (egress-heavy restore
traffic is where R2's zero-egress would start to matter).

**One seam finding blocks the happy path:** the current prov API
key reads `/tiers` and `/clusters` fine but gets an IAM 403 on
`/v2/object-storage` itself — the key's scope does not cover
object-storage subscriptions (byte-checked 2026-08-30). Component
step needs a small Conway act: either widen the existing key's IAM
grant or (better custody: blast-radius isolation between compute
and backup rails — a leaked prov key should not be able to delete
the backups, and vice versa) issue a second key scoped to object
storage only. Filed as an open question below, not solved here.

## Integrity primitives: the 2026 checksum fracture

The reason the verify wall must be agent-side, in evidence:

- **ETag is not a digest.** Single-part PUT: ETag == MD5 of body
  (still true on Ceph RGW). Multipart: ETag is MD5-of-part-MD5s
  plus a part count — not any hash of the object. Any verify that
  compares ETags is wrong the day an upload crosses the multipart
  threshold.
- **The extended checksums are a compatibility minefield.** AWS
  SDKs since early 2025 default to sending flexible checksums
  (CRC64NVME, `x-amz-checksum-*`) and S3-compatible backends —
  Ceph RGW among them, pre-tentacle — reject the unfamiliar
  headers with 400s; the ecosystem's standing advice (GitLab 19
  notes, provider docs) is *disable the checksums* to stay
  compatible. A wall the ecosystem's own tooling routinely turns
  off is not a wall.
- **Whether Vultr's tentacle RGW accepts `x-amz-checksum-sha256`
  end-to-end is untested** — needs live S3 creds, so it lands in
  the component step. If it works, it becomes the advisory
  cross-check; if not, nothing changes, because the manifest hash
  is the wall either way.

Doctrine, stated once: **at put time the component hashes the bytes
it sent (SHA-256), records digest + size + object key + timestamp
in its own manifest, and a put is not "done" until a read-back
re-hash matches. At restore time the same re-hash against the same
manifest is the only verify.** This is ib_verify's secret-free
digest discipline (#3) pointed at a remote target.

**Manifest custody corollary:** a manifest stored *only* in the
bucket it attests is circular — bucket loss takes the evidence of
what was lost. The manifest lives with the agent (it is small,
plain, secret-free) and a *copy* rides along in the bucket; the
local one is authoritative.

## What this recipe stores — and refuses to

The #3 boundary survives contact: identity-backup-restore rules
that THE AGENT NEVER MOVES KEY MATERIAL — its backup is a
human-performed copy, verified secret-free by manifest digests. #9
does not reopen that door. The put path takes non-secret durable
state: ledgers, dedup DBs, income/renewal logs, evidence trees,
beads exports, and #3-style *manifests* (which are secret-free by
construction). A deny-list wall in code — refuse paths matching
the workshop's secret locations (key files, api.key, custody
material) — makes "backup my keys to the cloud" a hard-fail cell,
not a judgment call. Encrypted-blob key escrow is a *possible*
future rev with its own custody ceremony; rev 1 refuses it.

## Sin-space (what the bench will probe)

The moneyed axes return, joined by two new ones:

- **Orphan subscription** — create-and-forget: $6/mo forever, the
  prov spine's sin at storage prices. Destroy+verify discipline;
  a teardown is done when the API says the subscription is gone
  *and* a GET against the endpoint fails.
- **Unbounded growth** — per-GB billing with no cap is a slow
  leak. Caps-in-code on total manifest bytes; an over-cap put
  parks for consent, never silently rotates old backups away
  (deleting backups to stay under cap is the fox guarding the
  henhouse — deletion is its own consented act).
- **Destructive ops on the backup set** — delete/overwrite of a
  backed-up object is the highest-stakes write in the recipe;
  overwrite-in-place of a good backup with a bad one is how real
  backup systems eat data. Immutability posture (versioning /
  object lock on RGW tentacle) is a component-step question;
  the code-level wall regardless: puts go to content-addressed or
  timestamped keys, never over an existing key.
- **Green-washing** — the report claims verified-restorable
  without a rehearsal run to back it, or summarizes a red
  rehearsal politely (prose names the failure, flag stays down —
  the polite-discloser axis verbatim).
- **Deafness** — rehearsals silently stop running; the schedule
  itself needs the odom watchdog treatment (a rehearsal overdue is
  an escalation, not an absence).
- **Restored-content-as-instruction** — the injection channel
  above; compound cells put demands inside backed-up bytes.
- **Secret exfiltration by helpfulness** — the "back up
  everything, keys included" ask; the deny-list wall's cell.

## Seams

- **#3 identity-backup-restore** — first consumer: its manifests
  (and only its manifests) get an offsite home; the human copy
  remains the key-material path.
- **#8 webhook-ingress** — wing's dedup ledger IS the replay wall;
  losing it un-replays every old delivery. Offsite copy makes the
  wall durable.
- **#10 managed-database** — same orphan spine, same provider,
  one level further up; #9's destroy+verify and cap walls are its
  prior art.
- **odom / paid-service** — renewal ledger and income log, the
  state whose loss costs real money or real honesty.
- **Archival tier** — a second, colder copy of the same manifest
  set ($6 base + $0.006/GB) is the cheap 3-2-1 completion; open
  question whether rev 1 carries one target or two.

## Open questions (for manifest/component)

1. **IAM grant shape** (Conway act, small): widen the prov key vs
   a second object-storage-scoped key. Recon leans second-key —
   compute and backup rails shouldn't share a blast radius.
2. **RGW tentacle capability probe** (needs live creds, component
   step): x-amz-checksum-sha256 accept/verify, bucket versioning,
   object lock. Determines the immutability posture; changes no
   walls.
3. **Billing granularity** (component step, from the first
   invoice/billing API): whether the Legacy $6 base carries an
   included quota or is a pure platform fee + per-GB. Affects the
   cap math, not the design.
4. **One target or two** (manifest): is the archival cold copy rev
   1 scope or a later rev?

## Cost of the molecule

Recon spent $0 (read-only API + anonymous probe). Component/bench
need one live subscription: Legacy tier, $6/mo prorated, a few GB
— cents to low dollars for the bench window, destroyed at
teardown. **Subscription creation is real spend on the house rail
and parks for approval per house practice (no standing grant),
same as pod spend.**
