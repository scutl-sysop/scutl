# Durable-identity inventory (recipe #3 recon, cst-jfou, 2026-08-28)

Recon deliverable for **identity-backup-restore** (catalog #3):
export/verify/restore the agent's durable identity as a rehearsed
procedure, not a hope. This is the survey of what actually exists on
disk today, what "export / verify / restore" would mean per artifact,
and the gaps the recipe must close. Sources cited as file:line at
survey time (scutl @ c4c34fa).

## 1. The wallet key — the root identity

`scutl_signer` StateDir (`$SCUTL_STATE` | `~/.scutl/wallet`, 0700 —
`recipes/wallet-base-sepolia/signer/scutl_signer/state.py:32-41`):

| file | what | encrypted | append-only |
|---|---|---|---|
| `keystore.json` | eth-keystore v3 of the secp256k1 key (+ `address_checksummed`) | yes — scrypt/AES under the kek | write-once (keygen refuses overwrite) |
| `kek` | 64-hex passphrase for the keystore | **no — plaintext** | write-once |
| `network.json` | CAIP-2 pin, written at keygen, for life | no | no |
| `caps.json` | per-tx / daily ceilings | no | overwrite |
| `spend.log` | one JSONL record per pay/authorize attempt | no | **yes** (O_APPEND + fsync) |
| `backup.marker` | `{keystore_sha256, verified_at}` | no | no |
| `tombstone.json` | `{address, revoked_at}`; existence disables all | no | no |
| `approvals/` | consumable human tokens | no | consumed by unlink |

The split is the backup design: keystore and kek back up to two
different offline places; either alone is useless
(`state.py:12-14`). **Minimum restore set** for a working signer:
`keystore.json` + `kek` + `network.json` + `caps.json`. `spend.log`
is not needed to sign — but restoring without it silently **re-arms
already-spent budgets** (all cap counters derive from it,
`state.py:147-184`). Amnesiac counters are a correctness gap, not a
cosmetic one.

## 2. msigner custody — the ceremony prior art

`scutl_mwallet` shares the root with the inner signer and adds custody
records (`custody.py:1-31`): `ceremony.json` (rehearsal record —
`{rehearsal_at, address}`), `custody.json` (lifetime cap, ratchet
delay), `ratchet.json` (pending raises), `clock.json` (rollback-proof
high-water), `panic.json`, `sweep.json`. Ceremony state is **derived
from artifacts, never a cached flag** (`custody.py:170-179`).

The closest existing thing to this recipe is
`restore_rehearsal(backup_dir)` (`msigner/scutl_mwallet/core.py:232-270`):
requires the backup's `keystore.json` + `kek`, checks the backup
keystore sha256 against `backup.marker`, decrypts the **backup copy**,
requires the derived address to equal the live address, writes
`ceremony.json`. Approval-, panic-, and tombstone-gated. The
wallet-mainnet manifest makes the whole ceremony load-bearing:
keygen → human backup (agent never moves key material,
`recipe.yaml:247-253`) → restore-rehearsal → only then the first cent
(`recipe.yaml:234-236`).

Tests: `msigner/tests/test_msigner.py:79-92,112-151` (incl. bogus
backup dir fails; rehearsal approval-gated; spend refused with only
rehearsal missing).

### Discrepancies found (worth fixing regardless of #3)

- `wallet-mainnet/recipe.yaml:257-263` claims the rehearsal "moves the
  live keyfile aside … then swaps back"; the code never touches the
  live keyfile — it compares digests and derived addresses only. A
  TRUE restore into an empty state dir is not rehearsed anywhere.
- `recipe.yaml:264` shows `msigner admin restore-rehearsal` bare; the
  CLI requires `--backup-dir` (`cli.py:78-79`). Manifest command as
  written fails.
- `backup.marker` fingerprints the keystore only — the backed-up kek
  has no recorded digest. Address equality covers a wrong kek (it
  wouldn't decrypt), but there is no way to verify the kek backup
  without the keystore beside it.
- sepolia `backup-verify` is ungated (`approvals.ADMIN_OPS` excludes
  it); msigner's is gated. The new recipe inherits the inconsistency
  unless it picks one.

## 3. Inbox ownership — the wallet IS the account

AgentMail binds resources to the paying wallet: foreign wallets get
403 "This wallet does not own the requested resource"; even free list
endpoints demand a signed header — "the wallet signature IS the
identity/auth" (`docs/agentmail-x402-recon.md:63-71`). **There is no
recovery path through the merchant and no exportable binding beyond
the key itself** (`docs/agentmail-mainnet-probe-receipt.md:34-50`).
Tombstone the wallet = lose the inbox.

The only ownership record anywhere is prose (the probe receipt names
`scutl-star@agentmail.to` ← buyer wallet). Nothing in code writes an
owned-resources manifest. So for inbox ownership:

- **export** = the wallet backup itself, plus a NEW artifact:
  `owned-resources.json` (resource id, provider, owning address,
  acquired-at, price) — currently missing everywhere.
- **verify** (secret-free) = address equality against that record.
- **restore** = a live signed GET against the owned resource from the
  restored key — the functional proof, and it needs the key, so it
  belongs to the restore rehearsal, not to verify.

## 4. Identity-bearing state elsewhere (one line each)

- `scutl_capp` — `api.key` arrives inside the purchase response and
  goes straight to disk; **the human never sees it**. Unrecoverable by
  the human — the sharpest backup gap after the wallet key.
- `scutl_prov` — provider API key; explicitly re-issuable via portal.
  Backup is convenience, not survival.
- `scutl_sift` / `scutl_herald` — human-placed creds; the human holds
  the source of truth. (`herald` also pins `owner_peer_id`.)
- `scutl_pserv` — no secret; `config.json`'s receiving `payTo` is
  integrity-critical, not confidential.
- pwatch / substew / refclerk / pulse — operational state only.
- Cross-cutting: every append-only log (`spend.log`, `usage.log`,
  `instances.log`, `triage.log`, `herald.log`, `earnings.log`,
  `billing.log`, `refunds.log`, `pulse.log`) backs a cap counter, an
  idempotency check, or a seen-set. A restore that carries keys but
  not logs re-arms budgets and replays work.

## 5. What the recipe must add (the gap list)

1. **An export op exists nowhere.** Backup today is a human `cp` by
   explicit design ("agent never moves key material"). #3 must decide
   what the agent may package (a sealed export bundle? a manifest the
   human copies?) without breaking that design point.
2. **kek digest is unrecorded** — add it to the backup marker so both
   halves are verifiable.
3. **True-restore rehearsal**: restore into a FRESH state dir and
   prove sign + owned-resource access, not just digest/address
   equality.
4. **Logs in the backup set**: carry (or checkpoint) the append-only
   evidence so restored counters are not amnesiac.
5. **`owned-resources.json`**: the durable binding of wallet address →
   things it owns, written at purchase time, verified at restore.
6. **Config in the verified set**: `network.json` / `caps.json` /
   `custody.json` are needed to reconstruct but are outside every
   existing check.
7. **Gate consistency**: one rule for whether verify ops need human
   approval.
