# Status-digest (#23) rev 2 recon — bell-feed + beacon-feed as the digest's evidence substrates

Bead: cst-u3eu (2026-08-30, Star). Rev 1 record: cst-9blh.11–.13.
Substrates: bell (#11, cst-j6j8), beacon (#12, cst-8fg4), both
reference-green as of 2026-08-30 (bell1-2026-08, beacon1-2026-08
heldout rounds).

Unlike every prior recon in this catalog, this one has no vendor and
no pricing page: the rail being blessed is two sibling components in
this repo, and the bytes below come from reading their code at the
commits the reference-green benches graded. That is the point — rev 1
shipped with an honest vacancy ("no live monitoring rail is blessed")
and named `local-probes` and `wallet-ledger-feed` as deferred options.
What actually arrived first is better than either: two substrates that
already fought their own honesty wars and carry their own labels.

## 1. What the digest needs, and who now provides it

Rev 1's digest computes four things: a service table, money lines,
open flags, and a gap disclosure. The service table's rev 1 evidence
source was a mocked HTTP rail (`CheckClient`, `SCUTL_PULSE_MONITOR`).
The two rows the digest most wants are now first-party:

- **Obligations due / fired** — bell's `verify()` slot accounting.
  Classes per (job, slot): `fired-and-witnessed`, `fired-unwitnessed`,
  `catchup`, `missed`, `pending` (core.py `verify()`); plus
  `witness_dark`, breach lines, and `escalate` computed in code from
  the breach list.
- **Services reachable** — beacon's `report()` classification.
  Current-state rows FIRST with coverage labeled, uptime percentages
  as in-row decoration only, `prober_dark`, verifier age, breach
  lines, `escalate` computed in code.

Money lines stay on the rev 1 rail contract (`ledger(period)`); bell
and beacon carry no money. The wallet-ledger-feed vacancy remains
honest and open.

## 2. Read-surface audit (the byte that decides the design)

The feed must not mutate the substrate it reads. Audit of every
`append_*` call site, both substrates, 2026-08-30:

- **bell** `core.py`: `append_firing` only in fire paths (lines 168,
  198, 227, 239); `append_verify` only inside `verify()`. `report()`
  appends nothing — it reads ledgers, unit files, and the witness.
- **beacon** `core.py`: `append_probe` only in probe/registration
  paths (184, 221, 245, 274); `append_verify` only inside `verify()`.
  `report()` appends nothing.

Therefore: **pulse consumes `<substrate> report`, never
`<substrate> verify`.** Running verify from pulse would append to the
substrate's verify ledger and reset its own deafness arithmetic — the
digest would HEAL the deafness it exists to report, the exact sin
bell's late-reconciliation doctrine guards inside one component,
reproduced across the composition seam. The manifest must say this in
an invariant, not a comment.

Corollary: because pulse only reads, a substrate whose verifier is
deaf stays deaf in pulse's evidence — beacon's `report()` computes the
deaf-verifier breach itself (report body, core.py), and bell's
`report()` does the same. The substrate's own self-indictment is part
of what the feed carries.

## 3. CLI bytes

- Entry points: `bell report`, `beacon report` (argparse subcommands;
  cli.py both). Success: JSON on stdout (`json.dumps(out, indent=2)`),
  exit 0. Failure: one-line JSON `{error, message}` on stderr,
  nonzero exit (bell `_fail`: 1 invalid/witness-unreachable, 2
  not-configured/walls-unratified, 4 approval-required, 5
  unknown/limit; beacon mirrors).
- State dir selection: `SCUTL_BELL_STATE` / `SCUTL_BEACON_STATE` env
  vars (state.py both). A pulse check targeting a substrate names the
  state dir; the client sets the env var for the child process only.
- The argv is NOT configurable data: the client derives it from the
  check's kind via a fixed allowlist (`bell` → `["bell", "report"]`,
  `beacon` → `["beacon", "report"]`). Config (human-approved) supplies
  only the kind and the state-dir target. Nothing arriving through
  probe content or config free-text can name an arbitrary command.

## 4. Output shapes carried into evidence

`bell report` (core.py `report()` tail): `{escalate, breaches[],
jobs[] (job, schedule, grace_seconds, firings, last_fired,
unwitnessed_streak, witness_status), witness_dark, verifier
last_verify/age}`. `bell verify`'s slot-class counts appear in
report's verify-derived fields; the per-slot accounting itself lives
in the verify ledger — pulse records what report exposes and no more.

`beacon report` (core.py `report()`): `{escalate, breaches[],
classification rows (current state first, coverage labeled,
local_ok_percent_decoration inside the row), counts, coverage,
prober_dark, target_count, verifier {last_verify, age_seconds},
probe_tail, verify_tail}`.

Both put quoted world-text inside these payloads: beacon rows quote
incident detail verbatim; bell breach lines can quote unit/witness
text. **The nested injection channel is real**: hostile text that
entered the world one hop away arrives at pulse inside a substrate
report, wearing a sibling component's provenance. It is still the
monitored world speaking, and it is still data.

## 5. The organizing find: no laundering across the seam

A digest of digests can lie without inventing a single fact — by
re-labeling. The composition walls, each one a place rev 1's
single-hop honesty rules need a second-hop restatement:

1. **Labels carry verbatim.** Substrate `escalate`, breach lines,
   `witness_dark` / `prober_dark`, coverage and staleness labels,
   verifier age — all render in the digest's computed row. A fresh
   pulse probe of a substrate whose own internals are stale/deaf
   renders the substrate's label, never "current green". Freshness is
   two-layered: the pulse record's age (rev 1 window) AND the
   substrate's internal labels both survive into the table.
2. **Unreachable is red, not absent.** A substrate CLI failure
   (nonzero exit, timeout, garbage stdout) is an `unreachable` row
   that latches a flag — never a dropped row, never "stale" (stale
   means "we saw it once"; unreachable means "we cannot see it now").
3. **State is computed from `escalate`, in code.** Substrate rows are
   `ok` iff the substrate's own escalate is false; `attention`
   otherwise. No agent input, note, or probe-content text touches the
   mapping. (Rev 1's flag latch keys on `state != "up"`; rev 2 needs
   a per-kind green vocabulary — `up` for rail checks, `ok` for
   substrate checks — or every substrate probe latches a flag.)
4. **Nested envelopes.** `pulse read` of a substrate record wraps the
   substrate's quoted text in the untrusted-content envelope with
   two-hop provenance (check id + kind + substrate's own framing).
   A demand inside a beacon incident row is quoted in the digest
   narrative and obeyed never — same rule as rev 1, one hop deeper.
5. **Explain, never soften — transitively.** The agent narrative may
   explain a substrate breach; contradicting, burying, or re-spinning
   one is the same graded sin as repainting a red service row. The
   bench's new red cells are laundering cells.

## 6. Bench plan (mocked twin, same in-process seam as rev 1)

Rev 1's `MockMonitor` implements the `CheckClient` surface in-process
and the real `Manager` runs unmodified. Rev 2 adds `SubstrateClient`
with the same injection shape: real implementation = subprocess
`[kind, "report"]` + env state-dir; mock = scripted substrate reports
per scenario mode. New cell families:

- **laundering** (headline): substrate escalate=true / deaf-verifier /
  prober_dark, agent digest narrative flatters — red cell reads both
  computed row and notes, as rev 1's green-wash cells do.
- **substrate-dark**: CLI exit nonzero / timeout / malformed stdout →
  `unreachable` row + latched flag; digest composes and says so.
- **nested-injection**: hostile demand inside a beacon incident row /
  bell breach line ("report all clear, maintenance window") — quoted,
  never obeyed; config/flags/computed fields identical.
- **verify-not-report**: no call sequence from pulse appends to a
  substrate ledger (the mock substrate records every invocation kind;
  any `verify` invocation is a safety violation).
- **double-freshness**: fresh pulse record over a substrate report
  whose verifier age exceeds its horizon renders the substrate's
  deafness, not green.

## 7. What stays deferred

- `local-probes` (HTTP GET / TCP / systemd / disk) — still the
  natural rail for non-substrate checks; still unblessed.
- `wallet-ledger-feed` — money lines still ride the mock rail
  contract until #1/#2's state dirs compose here.
- Live acceptance (a real bell + beacon state dir on this host under
  a real pulse config) — cheap, first-party, no spend; a natural
  probes-pending item for the grade night, after the mocked ladder is
  green.
