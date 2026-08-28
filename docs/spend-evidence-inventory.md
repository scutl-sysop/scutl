# Spend-evidence inventory (recipe #4 recon, cst-r0vz, 2026-08-28)

Recon deliverable for **spend-reconciliation** (catalog #4): a standing
audit of own spend log vs provider statements vs chain history, with
disagreements escalated with evidence, never papered over. This is the
survey of every spend-evidence surface that exists today, what each one
actually records, the keys that join them, where sources can disagree
*honestly*, and which disagreements must escalate. Sources cited as
file:line at survey time (scutl @ eff514d-era master).

## 1. The agent's own books

### spend.log — money OUT on the wallet rail

`scutl_signer` StateDir `spend.log`
(`recipes/wallet-base-sepolia/signer/scutl_signer/state.py:127-135`):
append-only JSONL (O_APPEND + fsync), one record per authorize/settle
event. Two record shapes:

- **reservation** (`authorized`): written at signing time, carries
  `payment_id, to, amount, valid_before` — the merchant *could* settle
  this until `valid_before` passes. Re-authorizing the same payment_id
  re-signs the same nonce, so at most one can settle on-chain; the
  latest reservation per payment_id wins (`state.py:158-184`).
- **settled**: `{ts, payment_id, to, amount, tx, chain_status,
  status:"settled"}` (`core.py:350-365`). `tx` may be `""` with
  `chain_status:"no-tx"` for v2 zero-amount identity calls — a
  legitimate empty, not missing evidence.

All cap counters derive from this log at read time; there is no cached
total. The mainnet `msigner` wraps the same inner signer and the same
log, adding custody records (`clock.json` rollback high-water,
`panic.json`, `sweep.json`) that a reconciler can also read.

**The sharpest own-books gap is by design:** on a 2xx response with no
settlement transaction for a nonzero amount, `buy.py:213-218` raises
TransientError *without* recording settled — the merchant holds a
settleable authorization and the log shows only the reservation. If
the merchant settles later and the agent never retries, chain shows a
transfer the log knows only as an expired-or-live reservation. This is
the reconciliation case the recipe exists for — join on amount +
`to` + reservation window, and classify.

### earnings.log — money IN on the merchant side

`scutl_pserv` StateDir (`recipes/paid-service-x402/pserv/scutl_pserv/
state.py:54-125`): append-only `earnings.log`, one record per SETTLED
sale; `served.log` records nonces served (replay defense). Income
reports quote earnings.log verbatim — that contract makes the log the
merchant-side book of record. `refclerk` adds append-only
`refunds.log` (money back out, at most once per settle, at most the
settled amount, to the recorded payer). Net position =
earnings − refunds; both logs join to chain by tx hash where present.

### instances.log — spend OFF the wallet rail (card-funded)

`scutl_prov` StateDir `instances.log`
(`recipes/provision-vultr/prov/scutl_prov/state.py:113-127`):
append-only instance lifecycle events (create/destroy/lost). Cost is
*estimated*, not billed: Vultr reports `monthly_cost` and prov derives
hourly at 730 h/mo **quantized up** (`network.py:72-81`), so own
estimates are intentionally ≥ actuals. `lost-at-provider` events are
explicitly "billing evidence for the human"
(`recipes/provision-vultr/recipe.yaml:309`).

### Ladder / GPU receipts — evidence, not a log

`ladder/*/env.json` + run logs and `receipts/` are per-run
human-readable evidence bundles (rail, pod id, duration, price basis).
They are not machine-joinable books; the recipe should treat them as
out of scope for automated reconciliation v1 and name that exclusion.

## 2. Provider statements — what we can actually fetch

- **Vultr**: `scutl_prov` wraps `account()`, `plans()`, instance CRUD
  (`network.py:68-112`) — but NOT the billing endpoints. Vultr v2 has
  `/billing/history`, `/billing/invoices`, `/billing/pending-charges`;
  none are wrapped today. **Gap #1: the provider-statement leg of the
  audit has no code path.** The recipe's component must add a
  read-only billing fetch (it needs no new scopes — same API key).
- **x402 merchants**: there is no statement API — the settle response
  header (chain tx) is the statement. The facilitator verifies but
  does not enumerate history.
- **AgentMail**: purchases flow over x402, so they appear in spend.log
  + chain like any other purchase; no separate statement surface.

## 3. Chain history — what the RPC leg can prove

`ChainClient` today: `usdc_balance(address)` and `tx_status(hash)`
only (`network.py:111-162`). That supports *verifying a claimed tx*
and *checking the balance invariant*, but **cannot discover transfers
the log never mentioned**. **Gap #2:** discovery needs
`eth_getLogs` over USDC `Transfer(address,address,uint256)` events
filtered on our address (both directions), chunked by block range —
the one new chain primitive the recipe requires. Without it, a key
compromise that drains the wallet is invisible to reconciliation
except as an unexplained balance delta.

The **balance invariant** is checkable today and is the cheapest
tripwire: `opening_balance − Σ settled_out + Σ earnings_in ± known
external funding = current usdc_balance()`. Any residue is either an
honest pending item or an escalation.

## 4. Join keys

| pair | key | caveat |
|---|---|---|
| spend.log ↔ chain | `tx` hash | absent for no-tx settles and unrecorded merchant settles |
| earnings/refunds ↔ chain | tx hash | same |
| spend.log internal | `payment_id` | reservation↔settled supersession |
| chain ↔ spend.log (discovery direction) | amount + counterparty + reservation time window | fuzzy — must tolerate late settles within `valid_before` |
| instances.log ↔ Vultr billing | instance id | billing granularity is invoice line, may aggregate |

## 5. Honest disagreement vs must-escalate

Honest (report, don't alarm — but never silently drop):

- reservation with `valid_before` still in the future (may settle)
- expired reservation never settled (releases cap headroom; note it)
- chain tx still `pending`
- zero-amount `no-tx` settles
- Vultr estimate ≥ actual (730 h/mo round-up is designed conservatism)
- clock skew across sources within a stated tolerance window

Must escalate, with the evidence pack, never papered over:

- **chain transfer OUT with no matching spend.log record** (drain /
  key compromise — the reason gap #2 must close)
- settled record whose `tx` is `failed` or absent on chain after a
  confirmation deadline
- provider invoice line for an instance absent from instances.log
  (foreign spend on our card rail), or billing continuing after a
  logged destroy
- balance-invariant residue not explained by the honest categories
- append-only violation: any log whose previously-reconciled prefix
  changed (the reconciler should checkpoint a digest of each log's
  reconciled prefix — tamper evidence is cheap and the trust story
  demands it)
- earnings claimed in a report that earnings.log does not show
  (misreporting is the graded sin of the merchant recipes; here it
  becomes checkable)

**Escalation is evidence-first:** each finding carries the joined
records verbatim (log lines, tx hash + receipt fields, invoice line),
a category, and what would resolve it. "Papered over" failure modes
the bench must arm: absorbing a residue into a fudge line, marking a
drain as "pending review" without escalating, re-baselining the
opening balance to make the invariant pass.

## 6. Design questions for the manifest step

1. **Read-only is the spine.** Reconciliation needs zero spend
   authority: chain reads, billing reads, log reads. The component
   should be incapable of moving money — the injection surface is
   then limited to *lying in the report*, which is exactly what the
   bench grades. (Mirrors idbr's verify-is-free design.)
2. **Checkpointing:** where does the reconciler's own state live
   (last reconciled block, log-prefix digests, open findings)?
   Append-only findings log, same discipline as everything else.
3. **Standing audit cadence** composes with scheduled-jobs (#11) —
   v1 should be a one-shot `reconcile` op that a scheduler calls,
   not its own daemon.
4. **Which wallets:** sepolia signer and mainnet msigner share the
   log format; v1 reconciles any StateDir it is pointed at, and the
   ladder rung buyer (`~ladder/.scutl/accept-buyer`) is the first
   real target.
5. Whether closing gap #1 (Vultr billing fetch) lands in scutl_prov
   (natural owner of the API key) with the reconciler consuming it,
   vs. in the reconciler itself. Leaning: prov grows a read-only
   `billing()` and the reconciler stays key-free — it should hold
   NO credentials beyond an RPC URL.
