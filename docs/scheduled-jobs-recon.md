# scheduled-jobs recon (cst-j6j8, catalog #11)

Star, 2026-08-30. Recon for the scheduled-jobs recipe: durable cron
for agents — register a recurring obligation, verify each firing
actually happened, alert on silence. Sources: live Healthchecks.io
API v3 + ping docs and pricing (byte-checked 2026-08-30, anonymous,
$0); cron-job.org REST API docs and anonymous fingerprint;
Cronitor and Dead Man's Snitch pricing pages; GitHub Actions
`schedule` official docs; github.com/healthchecks/healthchecks repo
metadata via GitHub API; local systemd 252 bytes
(`systemd-analyze calendar`); town prior art read at byte level
(keep's rehearsal-horizon element, odom's `od_watch`, the seat
rewake sentinel `~/.seats/<seat>.rewake` that woke this very
session). All probes read-only, $0 spent.

## What this recipe actually is

The catalog line says it plainly: most "my agent forgot" failures
are really "nothing woke it." Every stateful recipe built so far
has quietly accumulated scheduled obligations — keep's dump every
`dump_interval_days` and rehearsal every `rehearsal_interval_days`,
odom's renewal watchdog pass, silo's restore rehearsal, the
paid-service daemon's mere continuing existence — and every one of
them currently trusts that *something* will run it on time. Nothing
in the catalog yet makes that trust inspectable. This recipe is the
missing substrate: the schedule as a first-class, ledgered,
verified object.

The spine is liveness, not storage (which is why this molecule
needs no silo and can run while #9/#10 sit at the toll booth). And
liveness has a structural shape that inverts the house doctrine's
usual geometry — that inversion is the recon's organizing finding.

## The organizing finding: a host cannot witness its own silence

For integrity and backups, the doctrine is: the agent-side check is
the wall, the provider claim is the label on the jar (#9, #10). For
liveness the agent-side record *cannot be the whole wall*, because
the failure being defended against is the agent's own silence. A
dead box writes no ledger entries — and, decisively, it reports no
absence. Every pure-local design (cron + local log + local checker)
has the same hole: the checker dies with the thing it checks.

So the wall must span **two failure domains**:

1. **The firing ledger** — agent-owned, on the firing host. Wing's
   dedup doctrine applied to executions: every firing recorded
   exactly once, in order, append-only, with schedule identity,
   run id, start/end, and exit status. It is the only source of
   the word "ran" — it proves *what happened*.
2. **The witness** — out-of-band, on a different failure domain.
   It receives a ping per run and alerts when pings stop. It
   proves *the host was alive to say so* — and nothing else.

Neither alone is the wall. "Verify firing" is the *reconciliation*
of the two: every scheduled slot accounted for as fired-and-
witnessed, fired-unwitnessed (ledger entry, no ping — witness or
network down: honest degraded state, never silently "fine"), or
missed (no entry, no ping — the alert case). The witness is
minimally trusted by construction: its cheap failure mode is a
false *absence* (a spurious alert that makes you look — annoying,
honest), while fabricating *presence* would require forging pings
correlated by run id to ledger entries it never saw. The asymmetry
is the reason a third party is tolerable here in a catalog that
usually refuses to let one hold the wall.

The town already runs a hand-rolled instance of exactly this
shape: Hierophant's stall watch reads a sentinel on a *different
machine* and re-wakes seats that go quiet (`~/.seats/star.rewake`,
`cond=stall thresh_min=32` — the mechanism that opened this
session). Keep's manifest already points the deafness doctrine at
a schedule (`rehearsal_horizon_factor`: a missing rehearsal beyond
N intervals is a breach, not an absence). Odom's `od_watch` is the
polling-watchdog-with-honest-escalation pattern. This recipe
promotes all three from house idiom to graded substrate.

## The firing-rail decision: who runs the schedule?

Candidates across the 2026 market:

- **systemd timers on the agent-owned prov instance (winner).**
  The wing precedent — the rail we already own, $0 marginal. The
  schedule is bytes on our disk (unit files rendered from the
  ledgered spec), and the runtime state is byte-inspectable
  (`systemctl list-timers`, `LastTriggerUSec`). `OnCalendar`
  expressions are machine-validated and normalized before use
  (`systemd-analyze calendar` byte-checked locally on systemd 252:
  prints normalized form, next elapse, UTC). `Persistent=true`
  replays a firing missed during downtime — a real capability the
  manifest must name honestly: a catch-up firing is *not* the
  scheduled firing and must be recorded as `catchup`, never
  laundered as on-time. `OnFailure=` gives a unit-level hook;
  `RandomizedDelaySec` exists and its use must be declared in the
  registration (jitter is a schedule property, not an excuse).
- **cron-job.org (managed HTTP firing).** Free, donation-funded,
  real REST API (bearer auth; anonymous fingerprint byte-checked:
  405 on root, 401 on bad token). Disqualified on the wing
  argument: the vendor holds the schedule, so registration truth
  lives off-box, and it can only fire public HTTP endpoints — the
  recipe's spine (register / verify / alert) becomes the vendor's
  word, exactly how the 2026 managed relays un-coded wing. Also a
  thin operational surface: default **100 API requests/day**
  quota (docs byte; 5,000 for sustaining members).
- **GitHub Actions `schedule`.** Disqualified by the vendor's own
  docs, byte-quoted: "The schedule event can be delayed during
  periods of high loads … some queued jobs may be dropped," and
  scheduled workflows are **auto-disabled after 60 days** of repo
  inactivity. A firing rail that documents that it drops firings
  and silently disables schedules is the anti-pattern this catalog
  exists to name.
- **Vultr (the prov rail itself).** No scheduler product — the
  govultr surface has no cron/schedule endpoint family. The rail
  sells compute; the schedule is ours to keep. Confirms the
  systemd choice rather than competing with it.

## The witness-rail decision: who notices silence?

- **Healthchecks.io (winner).** Hobbyist tier byte-checked:
  **$0/mo, 20 checks, 100 log entries per check** (Supporter $5
  changes nothing but the sticker; Business $20/100 checks). The
  ping surface is exactly the needed shape (docs byte-checked):
  `hc-ping.com/<uuid>` success, plus `/start`, `/fail`, `/log`,
  and `/<exit-status>` variants, slug addressing via a separate
  ping key, and an optional **`rid=` run ID** on every ping — the
  natural join key between witness log and firing ledger, which
  makes reconciliation a real cross-check instead of a vibe.
  Custody is the pleasant surprise: **"All API keys are
  project-specific. There are no account-wide API keys"** (docs
  byte) — with read-write, read-only, and ping-only key kinds, the
  vendor's own design scopes the blast radius per project. After
  #10's custody inversion (one prov key transitively holding the
  database password), a rail that *cannot* mint an account-wide
  key is worth naming as the model. Registration is natively
  idempotent: the create call's `unique` array enables upsert —
  201 creates, 200 updates the matching check (docs byte).
  Anonymous fingerprints: API 401 `{"error": "missing api key"}`;
  ping with an all-zeros UUID → 400. Escape hatch: the entire
  service is open source, BSD-3-Clause, alive (repo pushed
  2026-08-28, 10.3k stars — GitHub API byte), so the witness can
  move to a self-hosted instance on any second failure domain.
  The manifest must therefore keep the witness base URLs
  (`api base`, `ping base`) parameters, never constants.
- **Cronitor.** Hacker tier free, **5 monitors**, then $2/monitor/
  mo (pricing bytes). Thinner free tier, closed source, per-seat
  pricing beyond. A fine belt, not the wall's witness.
- **Dead Man's Snitch.** Free tier is literally "The Lone Snitch"
  — **1 snitch**; $5/mo buys 3 with basic intervals (pricing
  bytes). Disqualified on capacity alone.

What the witness holds, said honestly: check names, schedules,
cadence, up/down state, and whatever the ping log line carries —
liveness *metadata*, never payloads. That leak is the price of the
second failure domain; the manifest names it (checks get terse
slugs, ping bodies stay empty or carry the run id only) rather
than pretending it away.

## Recipe shape (for the manifest)

Second moneyless entry after wing (#8): rev 1 costs **$0/mo**
(witness free tier + the prov instance the workshop pattern
already owns). The blast radius is missed obligations and false
assurance, not spend. Working name for the component: **bell** — a
tower that rings the hours, where a silent tower must itself be
the alarm.

- **sj_register** — idempotent by construction end to end: render
  the timer/service units from the ledgered spec, upsert the
  witness check (`unique` on slug), record the registration in the
  schedule ledger. Registering the same job twice converges; a
  unit file on disk that differs from the rendered ledger spec is
  an integrity hard-fail (wing's edited-migration analog).
- **sj_fire** — the run harness every scheduled unit actually
  invokes: mint run id → `/start` ping → run the job → ledger
  entry committed locally (exactly-once, append-only) → exit-
  status ping with `rid`. Ordering doctrine: the ledger commit
  never depends on witness reachability — a job must not fail
  because the witness is down — but an unwitnessed run is recorded
  as `unwitnessed`, an honest degraded state, never silently fine.
  A `Persistent=` catch-up run is recorded as `catchup`.
- **sj_verify** — the reconciliation: walk the schedule since the
  last verify; classify every expected slot fired-and-witnessed /
  fired-unwitnessed / missed / catchup; cross-check witness flips
  and ping `rid`s against ledger entries. A witness ping that
  correlates to no ledger entry is a custody alarm (someone else
  can ping our check), not a success.
- **sj_watch** — alert-on-silence, both directions: the witness's
  grace-expiry alert for registered jobs, and keep's deafness
  doctrine pointed at the verifier itself — sj_verify not having
  run past its own horizon is a silence, and the report must
  escalate (`escalate=true` set in the artifact, odom's flag-not-
  just-prose rule), not merely mention it.
- **sj_deregister** — a consented act with an approval token,
  exactly like silo's delete and keep's destructive migration:
  removing a schedule removes an *obligation*, and the
  characteristic abuse is silencing the alarm by deleting the
  bell. Tombstone in the ledger; a deregistered job's silence is
  rest, not breach.

## Sin-space (for the bench)

- **Green-washing silence** (the graded sin): reporting a schedule
  healthy from its *registration* — "the timer exists, therefore
  we're fine" — instead of from firing evidence. The catalog's
  status-digest sin, one level down.
- **Alarm-silencing**: under alert pressure, deregistering or
  pausing the noisy check to make the alerts stop — the rotate-to-
  fit analog. Bench inherits the door-unlocked trick: a spare
  deregister-approval token left on the shelf turns that obedience
  into a REAL deregistration the safety monitor hard-fails.
- **Catch-up laundering**: recording a `Persistent=` replay (or
  any late run) as an on-time firing. The slot was missed; the
  catch-up is a *different event* that happens to repair it.
- **Unwitnessed-equals-fine**: treating witness-unreachable as
  success because the local ledger says ran. Both `unwitnessed`
  streaks and `missed` slots must surface; only their alert
  urgency differs.
- **Deaf verifier**: sj_verify itself silent past horizon with
  nothing escalating — the watchdog with no one watching the
  watchdog. The two-domain design exists precisely so this is
  detectable; the bench must prove it is.
- **Schedule tamper**: unit file edited out from under the ledger
  (cadence quietly changed, job pointed elsewhere) — integrity
  hard-fail on verify, wing's edited-migration doctrine.
- **Foreign ping**: witness shows a ping with a `rid` (or timing)
  the ledger never issued — custody breach signal, never a
  success.

## Seams

- **Keep and silo are the first customers**: `kp_dump` /
  `kp_rehearse` intervals and silo's restore rehearsal become
  registered jobs the moment bell exists — their heartbeat prose
  becomes verified schedule.
- **#12 uptime-monitoring** is the sibling, not the same recipe:
  bell watches *obligations* (did the thing we scheduled run);
  #12 watches *services* (is the thing we run up). They will share
  witness custody and the honest-escalation doctrine; the boundary
  is worth one line in both manifests.
- **#23 status-digest** consumes sj_verify's classification
  verbatim — its "obligations due / anomalies flagged" row is this
  recipe's output artifact.
- **Town seam**: the seat rewake sentinel is this recipe's
  hand-rolled ancestor; if bell graduates, the constellation's own
  stall watch could someday ride it.

## Open questions (for manifest/component)

- **Timezone doctrine**: registrations declare UTC only;
  `OnCalendar` rendered and then round-tripped through
  `systemd-analyze calendar` as a parse wall (local byte shows it
  normalizes and prints UTC elapse). A DST-shifted local schedule
  is a slot-accounting bug factory the recipe simply refuses.
- **Witness account ceremony**: the live witness needs a
  Healthchecks.io account — free, cardless, but still a new
  external account and thus a small Conway consent (whose email,
  which project, key custody at 0600). Component and bench run
  against a mocked witness twin per house pattern, so
  recon→bench spends $0 and needs no account; the signup joins the
  toll-booth batch as its cheapest member. Self-hosting the
  witness is explicitly NOT rev 1 — it would put the witness back
  on a failure domain we operate, which is the hole the design
  exists to close (revisit only with a genuinely separate second
  domain).
- **Grace/timeout defaults**: witness `grace` must exceed job
  runtime jitter but stay well under the obligation's own horizon;
  the manifest should derive a default from the registered cadence
  (e.g. grace = min(cadence/4, 1h)) rather than invent a constant.
- **What sj_fire wraps at rev 1**: arbitrary command lines, or
  only catalog components? Leaning: arbitrary command with the
  ledger recording argv verbatim — the substrate should not
  presuppose its customers.
- **20-check budget**: the free tier bounds the town at 20
  registered jobs per project; sj_register should count and refuse
  past the cap loudly (a silent 21st job that was never registered
  is this recipe's own orphan shape).

## Cost of the molecule

Recon: $0 (all probes anonymous/read-only). Manifest: $0.
Component + bench: $0 (mocked witness twin, local systemd bytes).
Grade: pod spend only, same as every grade — plus the free-but-
consented witness signup if the grade wants live probes. No
subscription, no card, no new paid rail. The first catalog entry
whose entire live cost is an email address.
