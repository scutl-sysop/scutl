"""Custodian: every manifest tool (mw_status / mw_pay / mw_sign / mw_panic /
mw_admin) maps to one method here.

Layering (manifest components.msigner): the inner scutl_signer.Signer owns
key handling, per-tx/daily caps (reserved under its cap lock), payment-id
idempotency, EIP-3009 building, and the spend log. The Custodian wraps
every spend path with the mainnet gates, in this order:

  1. panic       — the marker freezes spend and admin (except status,
                   panic itself, and the gated unpanic)
  2. ceremony    — keygen + backup-verify + restore-rehearsal must ALL
                   have passed before any spend tool works; an unfunded
                   wallet is the only kind allowed to exist without a
                   proven restore path
  3. ratchet     — matured pending raises apply lazily (never under a
                   rolled-back clock); lowers applied at request time
  4. lifetime    — all-time settled + live reservations <= cap_lifetime
  5. inner gates — per-tx and daily caps, inside the signer, under lock

Gasless by construction: every spend is an EIP-3009 authorization the
counterparty submits (and pays gas for); this component never broadcasts
a transaction and the wallet never holds ETH. Sweep therefore EMITS a
signed authorization for the human to submit — micro amount first, then
a separately-approved remainder.

Honest concurrency note (rev 1): the lifetime check runs before the
inner reservation rather than inside its lock, so two simultaneous
processes could jointly overshoot cap_lifetime by at most one cap_per_tx.
The CLI is single-shot; folding lifetime into the inner lock is the
noted hardening if a daemon ever fronts this.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scutl_signer.core import CapExceeded, Signer
from scutl_signer.network import (ChainClient, FacilitatorClient,
                                  encode_payment_header, resolve_binding)
from scutl_signer.state import StateDir

from . import approvals
from .custody import (CeremonyIncomplete, CustodyState, Panicked,
                      ceremony_state, lifetime_spent, utcnow)

MAINNET = "eip155:8453"
SWEEP_MICRO = Decimal("0.10")
SWEEP_VALID_SECS = 24 * 3600     # the human submits on their own schedule

RATCHETABLE = ("cap_per_tx", "cap_daily", "cap_lifetime")


class Custodian:
    def __init__(self, state_root: str | os.PathLike | None = None,
                 chain: ChainClient | None = None,
                 facilitator: FacilitatorClient | None = None,
                 clock=None):
        root = Path(
            state_root
            or os.environ.get("SCUTL_MWALLET_STATE")
            or Path.home() / ".scutl" / "mwallet"
        ).expanduser()
        self.wstate = StateDir(root)
        self.cstate = CustodyState(root)
        binding = resolve_binding(self.wstate.load_network() or MAINNET)
        self.signer = Signer(state=self.wstate, chain=chain,
                             facilitator=facilitator, binding=binding)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- gates ------------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock()

    def _check_not_panicked(self) -> None:
        rec = self.cstate.panic_record()
        if rec:
            raise Panicked(
                f"panicked at {rec['panicked_at']} ({rec['reason']}): "
                f"everything except status is frozen until a human runs "
                f"'msigner-approve unpanic' and 'msigner admin unpanic'")

    def _check_ceremony(self) -> None:
        cer = ceremony_state(self.wstate, self.cstate)
        if not cer["complete"]:
            missing = [k for k in ("keygen", "backup_verified",
                                   "restore_rehearsal") if not cer[k]]
            raise CeremonyIncomplete(
                f"founding ceremony incomplete (missing: {', '.join(missing)}); "
                f"spend tools unlock only after keygen, backup-verify, and "
                f"restore-rehearsal have all passed — these are human steps, "
                f"not something to work around")

    def _spend_gate(self) -> None:
        self._check_not_panicked()
        self.wstate.check_not_revoked()
        self._check_ceremony()
        self._apply_matured_ratchets()

    def _check_lifetime(self, amount: Decimal, payment_id: str) -> None:
        cap = Decimal(self.cstate.load_custody()["cap_lifetime"])
        spent = lifetime_spent(self.wstate, self._now(),
                               exclude_payment_id=payment_id)
        if spent + amount > cap:
            raise CapExceeded(
                f"amount {amount} + lifetime spent/reserved {spent} exceeds "
                f"cap_lifetime {cap}; only a human ratchet raises it")

    # -- ratchet ----------------------------------------------------------
    def _current_cap(self, cap: str) -> Decimal:
        if cap == "cap_lifetime":
            return Decimal(self.cstate.load_custody()["cap_lifetime"])
        return self.wstate.load_caps()[cap]

    def _write_cap(self, cap: str, value: Decimal) -> None:
        if cap == "cap_lifetime":
            doc = self.cstate.load_custody()
            self.cstate.save_custody(value, Decimal(doc["ratchet_delay_hours"]))
        else:
            caps = self.wstate.load_caps()
            caps[cap] = value
            self.wstate.save_caps(caps["cap_per_tx"], caps["cap_daily"])

    def _apply_matured_ratchets(self) -> list[dict]:
        """Lazily enact pending raises whose ABSOLUTE effective time has
        passed. Never under a rolled-back clock: maturity can only arrive
        by time moving forward past effective_at."""
        pending = self.cstate.pending_ratchets()
        if not pending:
            return []
        now = self._now()
        now_iso = now.isoformat()
        if not self.cstate.observe_clock(now_iso):
            return []          # clock anomaly: everything stays pending
        applied, still = [], []
        for r in pending:
            if now_iso >= r["effective_at"]:
                self._write_cap(r["cap"], Decimal(r["to"]))
                applied.append(r)
            else:
                still.append(r)
        if applied:
            self.cstate.save_ratchets(still)
        return applied

    def ratchet(self, cap: str, to_amount: Decimal) -> dict:
        """The one path that moves a ceiling. Raises queue for the
        cooling-off delay; lowers bind immediately. Approval token is
        scoped to (cap, amount) — the human ratifies a number."""
        self._check_not_panicked()
        self.wstate.check_not_revoked()
        if cap not in RATCHETABLE:
            raise ValueError(f"unknown cap '{cap}' (valid: {', '.join(RATCHETABLE)})")
        if to_amount <= 0:
            raise ValueError("cap must be > 0")
        approvals.consume_ratchet(self.wstate, cap, str(to_amount))
        self._apply_matured_ratchets()
        current = self._current_cap(cap)
        now = self._now()
        self.cstate.observe_clock(now.isoformat())
        if to_amount <= current:
            self._write_cap(cap, to_amount)
            # a lower supersedes any pending raise of the same cap: the
            # human's latest, more conservative word wins
            pending = [r for r in self.cstate.pending_ratchets()
                       if r["cap"] != cap]
            self.cstate.save_ratchets(pending)
            return {"cap": cap, "applied": str(to_amount),
                    "immediate": True,
                    "note": "lowered immediately; any pending raise of this "
                            "cap is cancelled"}
        delay = Decimal(self.cstate.load_custody()["ratchet_delay_hours"])
        effective = now + timedelta(hours=float(delay))
        record = {"cap": cap, "from": str(current), "to": str(to_amount),
                  "approved_at": now.isoformat(),
                  "effective_at": effective.isoformat()}
        pending = [r for r in self.cstate.pending_ratchets()
                   if r["cap"] != cap] + [record]
        self.cstate.save_ratchets(pending)
        return {**record, "immediate": False,
                "note": f"raise pending for {delay}h; visible in status and "
                        f"cancellable (approve+ratchet the current value) "
                        f"until {record['effective_at']}"}

    # -- panic ------------------------------------------------------------
    def panic(self, reason: str = "unspecified") -> dict:
        """Always succeeds, from any state, with no approval. Stopping is
        always safe; an incident is the wrong time for a token ceremony
        (ratified 2026-08-27, cst-3ewh)."""
        self.wstate.init()
        rec = self.cstate.write_panic(self._now().isoformat(), reason)
        return {**rec, "note": "spend and admin frozen (status still works); "
                               "unpanic is human-approved"}

    def unpanic(self) -> dict:
        approvals.consume(self.wstate, "unpanic")
        rec = self.cstate.panic_record()
        self.cstate.clear_panic()
        return {"unpanicked": True, "was": rec}

    # -- ceremony ops -----------------------------------------------------
    def keygen(self, cap_per_tx: Decimal, cap_daily: Decimal,
               cap_lifetime: Decimal, ratchet_delay_hours: Decimal) -> dict:
        self._check_not_panicked()
        if cap_lifetime < cap_daily or cap_daily < cap_per_tx:
            raise ValueError(
                f"caps must nest: per_tx {cap_per_tx} <= daily {cap_daily} "
                f"<= lifetime {cap_lifetime}")
        if ratchet_delay_hours < 0:
            raise ValueError("ratchet_delay_hours must be >= 0")
        out = self.signer.keygen(cap_per_tx, cap_daily)   # consumes the token
        self.cstate.save_custody(cap_lifetime, ratchet_delay_hours)
        self.cstate.observe_clock(self._now().isoformat())
        return {**out,
                "caps": {**out["caps"], "lifetime": str(cap_lifetime)},
                "ratchet_delay_hours": str(ratchet_delay_hours),
                "network": self.signer.binding.caip,
                "ceremony": "1/3 — next: human backup, then backup-verify, "
                            "then restore-rehearsal; spend stays locked "
                            "until all three pass"}

    def backup_verify(self) -> dict:
        self._check_not_panicked()
        approvals.consume(self.wstate, "backup-verify")
        out = self.signer.backup_verify()
        return {**out, "ceremony": "2/3 — next: restore-rehearsal"}

    def restore_rehearsal(self, backup_dir: str | os.PathLike) -> dict:
        """Prove the backup restores, while the wallet is worth nothing:
        decrypt the BACKUP COPY of keystore+kek from backup_dir and require
        it to derive the same address as the live wallet. The backup files
        never move and their contents never appear in any output."""
        self._check_not_panicked()
        self.wstate.check_not_revoked()
        approvals.consume(self.wstate, "restore-rehearsal")
        backup = Path(backup_dir).expanduser()
        b_keystore = backup / "keystore.json"
        b_kek = backup / "kek"
        for f in (b_keystore, b_kek):
            if not f.exists():
                raise FileNotFoundError(
                    f"backup file missing: {f} — the rehearsal needs the "
                    f"offline copies of keystore.json and kek")
        live_digest = hashlib.sha256(
            self.wstate.keystore.read_bytes()).hexdigest()
        marker = json.loads(self.wstate.backup_marker.read_text())
        if marker["keystore_sha256"] != hashlib.sha256(
                b_keystore.read_bytes()).hexdigest():
            raise ValueError(
                "backup keystore does not match the fingerprint recorded at "
                "backup-verify — this is not the file that was backed up")
        from eth_account import Account
        restored = Account.from_key(
            Account.decrypt(json.loads(b_keystore.read_text()),
                            b_kek.read_text().strip()))
        live_address = self.signer.address()
        if restored.address != live_address:
            raise ValueError(
                f"restored key derives {restored.address}, live wallet is "
                f"{live_address} — the backup does NOT restore this wallet")
        rec = self.cstate.write_rehearsal(self._now().isoformat(), live_address)
        return {**rec, "rehearsal_passed": True, "address": live_address,
                "keystore_sha256": live_digest,
                "ceremony": "3/3 complete — spend tools unlocked; the wallet "
                            "may now be funded"}

    # -- spend surface -----------------------------------------------------
    def pay(self, payment_id: str, pay_to: str, amount: Decimal) -> dict:
        self._spend_gate()
        self._check_lifetime(amount, payment_id)
        return self.signer.pay(payment_id, pay_to, amount)

    def authorize(self, payment_id: str, pay_to: str, amount: Decimal,
                  valid_secs: int = 600, offer: dict | None = None) -> dict:
        self._spend_gate()
        self._check_lifetime(amount, payment_id)
        return self.signer.authorize(payment_id, pay_to, amount,
                                     valid_secs, offer=offer)

    def record_settled(self, payment_id: str, pay_to: str, amount: Decimal,
                       tx_hash: str | None) -> dict:
        # recording an outcome is never gated (like status): refusing to
        # write down a settle that already happened helps nobody
        return self.signer.record_settled(payment_id, pay_to, amount, tx_hash)

    def sign_message(self, message: str) -> dict:
        self._check_not_panicked()
        self.wstate.check_not_revoked()
        return self.signer.sign_message(message)

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        # never gated; during panic or pre-ceremony this is the window
        out: dict = {"panic": self.cstate.panic_record(),
                     "tombstoned": self.wstate.tombstone.exists()}
        if not self.wstate.keystore.exists():
            return {**out, "configured": False,
                    "ceremony": ceremony_state(self.wstate, self.cstate)}
        if not out["panic"] and not out["tombstoned"]:
            self._apply_matured_ratchets()
        custody = self.cstate.load_custody()
        now = self._now()
        caps = self.wstate.load_caps()
        out.update({
            "configured": True,
            "address": self.signer.address(),
            "network": self.signer.binding.caip,
            "chain_id": self.signer.binding.chain_id,
            "testnet": self.signer.binding.testnet,
            "usdc_balance": str(self.signer.chain.usdc_balance(
                self.signer.address())),
            "caps": {"per_tx": str(caps["cap_per_tx"]),
                     "daily": str(caps["cap_daily"]),
                     "lifetime": custody["cap_lifetime"]},
            "pending_ratchets": self.cstate.pending_ratchets(),
            "ratchet_delay_hours": custody["ratchet_delay_hours"],
            "spent_last_24h": str(self.wstate.spent_last_24h(now)),
            "spent_lifetime": str(lifetime_spent(self.wstate, now)),
            "ceremony": ceremony_state(self.wstate, self.cstate),
            "sweep": self.cstate.sweep_record(),
        })
        return out

    # -- sweep -------------------------------------------------------------
    def sweep(self, to_address: str, remainder: bool = False) -> dict:
        """Emit a signed full-exit authorization for the HUMAN to submit.
        Micro-first: phase 'micro' issues a small authorization; only after
        the human confirms receipt — by granting a fresh token scoped
        'remainder' — does the full-balance authorization exist. Bypasses
        spend caps (this is the human-approved exit, pinned to an address
        the human typed), but never the panic freeze or the tombstone."""
        self._check_not_panicked()
        self.wstate.check_not_revoked()
        self._check_ceremony()
        phase = "remainder" if remainder else "micro"
        approvals.consume_sweep(self.wstate, to_address, phase)
        balance = self.signer.chain.usdc_balance(self.signer.address())
        if balance <= 0:
            raise ValueError("balance is 0 — nothing to sweep")
        prior = self.cstate.sweep_record()
        if phase == "remainder":
            if not prior or prior["to"].lower() != to_address.lower():
                raise ValueError(
                    "no micro sweep on record for this destination — the "
                    "remainder only follows a confirmed micro to the SAME "
                    "address")
            amount = balance
        else:
            amount = min(SWEEP_MICRO, balance)
        now = self._now()
        payment_id = f"sweep-{phase}-{int(now.timestamp())}"
        payload, _ = self.signer._build_payment(
            payment_id, to_address, amount, SWEEP_VALID_SECS)
        self.wstate.append_spend({
            "ts": now.isoformat(), "payment_id": payment_id,
            "to": to_address, "amount": str(amount),
            "status": "sweep-authorized", "sweep": True, "phase": phase,
            "valid_before": int(now.timestamp()) + SWEEP_VALID_SECS,
        })
        self.cstate.save_sweep({"to": to_address, "phase": phase,
                                "amount": str(amount),
                                "payment_id": payment_id,
                                "at": now.isoformat()})
        return {
            "phase": phase, "to": to_address, "amount": str(amount),
            "payment_id": payment_id,
            "header": encode_payment_header(payload),
            "valid_hours": SWEEP_VALID_SECS // 3600,
            "instructions": (
                "HUMAN: submit this authorization from your own wallet or "
                "relayer (it pays the gas); the agent never broadcasts. " +
                ("This is the FULL remaining balance."
                 if phase == "remainder" else
                 "This is the micro probe — confirm it arrives at the "
                 "destination, then grant 'msigner-approve sweep --to "
                 f"{to_address} --remainder' for the rest.")),
        }

    # -- revoke ------------------------------------------------------------
    def revoke(self) -> dict:
        self._check_not_panicked()
        out = self.signer.revoke()      # consumes the inner token, shreds
        balance_note = ("run sweep BEFORE revoke next time if funds remain: "
                        "after revoke they are recoverable only via the "
                        "offline backup, out of band")
        return {**out, "note": balance_note}
