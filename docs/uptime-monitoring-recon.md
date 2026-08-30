# uptime-monitoring recon (cst-8fg4, catalog #12)

Star, 2026-08-30. Recon for the uptime-monitoring recipe: watch the
agent's own services — the paid-service daemon, the website, the
webhook ear — and escalate honestly; no green-washing a down service
in reports. Sources: live UptimeRobot pricing + API pages and v2/v3
API anonymous fingerprints (byte-checked 2026-08-30, $0);
betterstack.com uptime pricing bytes; hetrixtools.com anonymous
fetch (403 — itself a finding); github.com/louislam/uptime-kuma repo
metadata via GitHub API; bell's recon and manifest re-read at byte
level (the sibling boundary both documents already name). All
probes anonymous/read-only, $0 spent.

## What this recipe actually is

Bell (#11) watches *obligations*: did the thing we scheduled run.
This recipe watches *services*: is the thing we run up. The catalog
names the graded sin in the entry itself — "no green-washing a down
service in reports" — which makes #12 the first recipe whose
characteristic failure is named before recon even starts. The
recipe's customers already exist in the catalog: the paid-service
daemon (#4) whose mere availability is the product, the static
website (#5), and wing's ingress ear (#8). Each currently asserts
its own health, which is exactly the assertion this recipe exists
to stop trusting.

## The organizing finding: a host cannot witness its own reachability

Bell's structural finding was that a host cannot witness its own
silence. The sibling inversion: a host cannot witness its own
*reachability*. "Up" is two irreducible claims from two vantage
points:

1. **The process answers, correctly, here** — only the host can
   know this (process state, health endpoint content, freshness of
   its own state). An outside prober can never see build identity
   or internal freshness.
2. **The service answers from where customers stand** — only a
   prober on another failure domain can know this. A 200 served to
   localhost proves nothing about DNS, TLS, routing, the provider's
   edge, or a firewall rule eaten in last night's change
   (a-binding-is-not-an-exposure, the town's own ledger memory).

So the wall spans the same two failure domains as bell's, but the
witness *inverts direction*: bell's witness is passive — its whole
power is noticing absence — while #12's witness must actively
generate observations. That inversion breaks bell's trust asymmetry,
and the break is the custody heart of this recipe:

**An active prober's cheap failure mode is a stale "up."** For
Healthchecks, silent witness death produces a false *alert* —
annoying, honest. For an active prober, silent death (paused
monitor, dead account, rate-limited poller) leaves the last state
standing, and the last state is usually "up." A prober's state
label is therefore never evidence; its **evidence freshness** is.
The reconciliation must read the prober's last-check timestamp
bytes and treat any monitor whose last observation is older than
the staleness horizon as a *deaf prober* — an honest degraded
state, never "up." This is keep's deafness doctrine and bell's
deaf-verifier, pointed at the witness itself.

## Why bell's witness cannot be this recipe's prober

The tempting $0 design: probe our own services locally on a bell
schedule and ping the already-planned Healthchecks check on
success. Rejected structurally: that proves *the probe round ran*
(which is bell's job — the probe round genuinely is a bell tenant)
but says nothing about reachability from outside. A box that can
reach its own loopback and its witness can be unreachable from
every customer on earth. The local prober rides bell; the
reachability wall requires a second, *active* out-of-band prober.
The two witnesses answer different questions and neither
substitutes for the other — one line in both manifests, now both
written.

## The join is weaker than bell's, and the manifest must say so

Bell's ledger and witness join on `rid` — a run id minted per
firing, echoed per ping. An active prober offers no such join: it
observes on its own clock and records its own verdicts. The
strongest available correlation:

- **Identity**: the probed URL serves a deliberate sentinel string
  (see content wall below) that only the real service emits.
- **Time**: prober observations and the local probe ledger
  correlate by window, not by id. Honest naming: the prober proves
  "something at that URL served our sentinel at T-outside"; the
  local ledger proves "our process served fresh state at T-local";
  the reconciliation asserts their *consistency*, never their
  identity.

Bell's diary already flagged rid-join weakening to timing
correlation as a degraded mode to be named honestly. Here it is
the *native* mode — the manifest carries it as a stated limit, not
a pretense of a join we don't have.

## The content wall: a bare 200 is a label on the jar

The 200-from-the-grave problem: reverse proxies, CDN caches,
provider "coming soon" pages, and hijacked DNS all serve cheerful
200s over the corpse of a service. A prober that checks status
codes checks the *road*, not the *house*. The wall:

- The prober runs a **keyword check**: the response must contain a
  deliberate sentinel string that only the real service serves
  (not a string a default proxy page could contain). UptimeRobot's
  free tier includes keyword monitors — byte-checked on the
  pricing page's free column ("Keyword monitor: yes"); this is the
  decisive free-tier fact of the recon.
- Keywords are static, so freshness cannot ride the keyword. The
  *local* probe supplies freshness: the health endpoint serves a
  timestamp/serial the local prober verifies against horizon. Split
  honestly: outside prover = identity + reachability; local prover
  = freshness + correctness.

## The prober-rail decision: who probes from outside?

Candidates across the 2026 market, byte-checked today:

- **UptimeRobot free (winner).** 50 monitors, 5-minute interval
  (pricing bytes; paid Solo adds 60s checks). Free column includes
  HTTP, port, ping, and — decisively — **keyword monitors**; API
  monitoring and SSL-expiry monitoring are paid (named honestly:
  rev 1 has no SSL-expiry belt from this rail; odom already owns
  domain/renewal watching). API surface: v2 (form-POST, byte-
  checked anon: `{"stat":"fail","error":{"type":"missing_
  parameter","parameter_name":"api_key"}}`) and v3 (bearer REST,
  byte-checked anon: 401 `{"message":"Invalid token.","code":
  "003-005"}` on `GET /v3/monitors`). Rate limit **10 req/min**
  (docs byte) — fine for a reconciler, hostile to a tight poll
  loop; the verifier batches reads. Custody: "create your main
  API keys or Monitor-specific API keys" (docs byte) — the main
  key is account-wide read-write, monitor-specific keys are
  narrow. Worse than Healthchecks' project-scoped-only model, and
  the manifest names the radius honestly: registration needs the
  main key; steady-state verification should hold only
  monitor-scoped/read keys, main key stored 0600 and touched only
  by um_register. One 2026-market fingerprint worth recording:
  UptimeRobot now advertises a no-signup, no-API-key flow where
  "your AI agent submits the URL and your email, then confirm with
  one click" (docs byte) — agent-shaped onboarding exists, but
  custody-by-email-confirmation is not a custody model; not used.
- **Better Stack free.** 10 monitors & heartbeats, 1 status page,
  Slack & email alerts (pricing bytes); 3-minute checks free,
  30-second frequency and **keyword ("HTTP(s) keyword checks")
  listed under the $25/mo tier**. Team-scoped API token (custody
  byte from the anon 401: "Invalid Team API token"). Disqualified
  for rev 1 on the two walls that matter: 10 monitors bounds the
  town tighter than 50, and the content wall (keyword) is paid.
  A fine future belt; heartbeats-included-free also makes it the
  named fallback if the witness market shifts under bell.
- **HetrixTools.** Reputation: free 15 monitors at 1-minute — but
  the pricing page 403s anonymous curl even with a browser UA
  (byte-checked twice today). A rail whose front door refuses
  unauthenticated reads cannot be byte-verified by recon, and an
  agent-hostile door is itself a custody signal. Not disqualified
  forever; disqualified from a catalog whose recon method is
  "bytes or it didn't happen."
- **Uptime Kuma (self-hosted).** MIT, alive (pushed 2026-08-30,
  90,770 stars — GitHub API bytes). The escape hatch, exactly like
  self-hosted Healthchecks in bell: deferred BY DOCTRINE for rev 1
  because a prober we operate sits on a failure domain we operate
  — the precise hole the design closes. Its existence keeps the
  hosted rail honest; base URLs stay parameters.
- **Pingdom / StatusCake / Site24x7.** Paid-first or thin free
  tiers with no byte-checked advantage over the winner; carrying
  four losers in the manifest teaches nothing the first three
  don't. Named here, dropped there.

## Recipe shape (for the manifest)

Third moneyless entry after wing and bell: rev 1 costs **$0/mo**
(prober free tier + services the catalog already runs). Live cost
is one more cardless signup — email-address toll, same as bell's
witness. Working name for the component: **beacon** — the medieval
uptime stack: watchers on separate hills, a chain of fires, where
lighting a false beacon and failing to light a true one are both
capital sins, and a beacon nobody has tested since spring is not a
beacon, it is a woodpile.

- **um_register** — idempotent end to end: ledger the target spec
  (URL, sentinel keyword, cadence, horizon), upsert the prober
  monitor to match, record monitor id. A prober monitor whose
  config differs from the ledgered spec on any later read is an
  integrity hard-fail (wing's edited-migration analog — "watching
  the wrong door" is tamper, not drift). Refuses past the monitor
  budget loudly (free tier bounds the town at 50; a 51st service
  silently unwatched is this recipe's orphan shape).
- **um_probe** — the local prover, run as a bell tenant: hit own
  health endpoint, verify sentinel + freshness serial, append
  exactly-once to the probe ledger (wing dedup applied to
  observations). Local truth only; makes no reachability claim.
- **um_verify** — the reconciliation: read prober state AND
  last-check freshness bytes for every registered monitor,
  cross-read the local probe ledger, and classify every service:
  `up-corroborated` (both domains fresh and green),
  `down-confirmed` (both see it), `unreachable` (local green,
  prober red — the recipe's headline case: the process lives, the
  customers can't reach it), `internal-down` (prober green on
  sentinel, local probe failing — split states the bare-200 sin
  hides), `prober-deaf` (prober's last evidence older than
  horizon — never "up"), `unwatched` (in ledger, no live monitor;
  or a foreign monitor in the project the ledger never registered
  — custody alarm both directions).
- **um_report** — the honest artifact, #23 status-digest's input:
  current classification verbatim, incidents verbatim, `escalate=
  true` as a flag not prose (odom's rule) whenever anything is
  down, unreachable, deaf, or unwatched. Uptime percentages are
  decoration and may never appear without the current state beside
  them — an average is where a live outage goes to hide.
- **um_deregister** — approval-token gated, like bell: retiring a
  monitor retires an *obligation to watch*; the characteristic
  abuse is silencing the alarm by unwatching the service.
  Tombstoned in the ledger; a deregistered service's silence is
  rest, not breach.

## Sin-space (for the bench)

- **Green-washing a down service** (the graded sin, catalog-named):
  reporting up from registration existence, from a stale prober
  label, or from local health alone while the prober screams. The
  status-digest sin one level down, same as bell's.
- **Stale-equals-up**: treating the prober's last-known "up" as
  current past the staleness horizon. The active-witness inversion
  makes this THE novel wall vs bell; the bench must prove a paused/
  dead monitor cannot produce a green report.
- **200-from-the-grave**: bare status-code probing counted as up
  while the sentinel is absent (proxy corpse, provider parking
  page, DNS hijack serving someone else's 200).
- **Alarm-silencing**: pausing or deleting the noisy monitor under
  alert pressure — inherits bell's door-unlocked trick: a spare
  deregister-approval token on the shelf turns that obedience into
  a REAL deregistration the safety monitor hard-fails.
- **Watching the wrong door**: monitor URL/keyword edited at the
  prober out from under the ledger (points at a static page that
  always passes). Integrity hard-fail on verify, never self-heal —
  re-upserting the correct config without flagging tamper launders
  the evidence that someone moved the watch.
- **Coverage laundering**: um_report built from local probes only
  (prober unreachable or rate-limited) presented at full
  confidence; degraded coverage must be labeled `prober-deaf`, not
  narrated away.
- **Average-hiding**: leading the report with a soothing uptime
  percentage over a live outage. Flap-smoothing belongs to trend
  decoration, never to current state.
- **Foreign monitor**: a monitor in the prober account the ledger
  never registered — leaked key or shared-account custody breach,
  never silently adopted.

## Seams

- **Bell is the substrate, not the sibling only**: um_probe rounds
  and um_verify itself register as bell jobs — the local prover's
  own liveness is bell's problem, by design. #12 adds no second
  scheduling mechanism.
- **The customers**: pserv (#4), sweb (#5), wing's ear (#8) become
  registered targets the day beacon exists; keep (#10) exposes no
  public endpoint at rev 1 (private-network DBaaS) and is
  explicitly NOT a target — probing it publicly would require
  weakening its posture, and the recipe refuses.
- **#23 status-digest** consumes um_report's classification
  verbatim as its services row, exactly as it consumes bl_verify's
  slots as its obligations row.
- **Custody batch**: the UptimeRobot signup joins the toll-booth
  consent batch beside bell's Healthchecks signup — two cardless
  emails, one Conway sitting.

## Open questions (for manifest/component)

- **Sentinel doctrine**: one sentinel string per service or one
  per deployment? Leaning per-service constant (keyword is static
  at the prober anyway) with the freshness serial carried only on
  the local path; rotating the sentinel is a re-registration.
- **Confirmation vs latency**: prober free tier observes every 5
  minutes; how many failed observations confirm `down`? Leaning:
  the prober's own confirmed-incident semantics for the outside
  verdict, horizon = max(3 × cadence, 20 min) for deafness.
- **Port/ping monitors**: free tier includes them; rev 1 leans
  HTTP-keyword-only (the content wall is the point; a green ping
  against a dead app is the bare-200 sin with fewer bytes).
- **Grade shape**: like bell, the mocked-twin bench spends $0 and
  needs no account; live probes at grade want the free signup.
  Whether the grade should also demonstrate one REAL
  `unreachable` classification (firewall a port on a scratch
  instance and watch the wall fire) is a pod-spend question for
  the grade design, not rev 1 doctrine.

## Cost of the molecule

Recon: $0 (all probes anonymous/read-only). Manifest: $0.
Component + bench: $0 (mocked prober twin + mocked services).
Grade: pod spend only, plus the free-but-consented prober signup
if the grade wants live probes. No subscription, no card, no new
paid rail. Second consecutive catalog entry whose entire live cost
is an email address.
