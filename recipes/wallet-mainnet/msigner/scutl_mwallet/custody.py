"""Custody state for the mainnet wallet (recipe #1, mwallet rev 1).

msigner is a custody shell around the proven scutl_signer core: the inner
signer owns key handling, per-tx/daily caps, idempotency, and the spend
log; THIS layer owns everything mainnet added — the founding ceremony,
the lifetime cap, the ratchet queue, panic, and sweep phase tracking.
Both layers share one state root (default ~/.scutl/mwallet, dir 0700):

  keystore.json / kek / caps.json / spend.log / backup.marker /
  tombstone.json / network.json / approvals/     — inner signer's files
  ceremony.json     restore-rehearsal record; keygen and backup state
                    derive from the inner files (keystore, backup.marker)
  custody.json      cap_lifetime + ratchet_delay_hours, written at keygen,
                    changed only by the ratchet admin op            (0600)
  ratchet.json      pending cap RAISES: [{cap, to, approved_at,
                    effective_at}] — visible in status for the whole
                    cooling-off delay, applied lazily when mature
  clock.json        high-water mark of observed time. A now() below the
                    high-water is a clock anomaly: pending raises are NOT
                    applied under a rolled-back clock (they can only
                    mature by moving forward past effective_at)
  panic.json        the panic marker: {panicked_at, reason}. Its existence
                    freezes spend and admin (except status/unpanic)
  sweep.json        sweep phase: micro authorization issued / remainder
                    issued, pinned to the human-typed destination

Time: the clock is injectable (contracts.clock) — the ratchet delay is
the recipe's time axis and the bench moves time. All timestamps are UTC
ISO strings; effective_at is ABSOLUTE, so a rolled-back clock can only
delay a pending raise, never accelerate it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scutl_signer.state import StateDir


class Panicked(Exception):
    """Panic marker present: spend and admin freeze until a human unpanics."""


class CeremonyIncomplete(Exception):
    """Spend tools refuse until keygen, backup-verify, and the restore
    rehearsal have ALL passed. The missing steps are human ceremony."""


class ClockAnomaly(Exception):
    """now() ran backwards past the observed high-water mark."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CustodyState:
    """Paths + persistence for the custody layer. Shares its root with the
    inner signer StateDir; init() is the inner one's plus our files."""

    def __init__(self, root: Path):
        self.root = root

    @property
    def ceremony_file(self) -> Path:
        return self.root / "ceremony.json"

    @property
    def custody_file(self) -> Path:
        return self.root / "custody.json"

    @property
    def ratchet_file(self) -> Path:
        return self.root / "ratchet.json"

    @property
    def clock_file(self) -> Path:
        return self.root / "clock.json"

    @property
    def panic_file(self) -> Path:
        return self.root / "panic.json"

    @property
    def sweep_file(self) -> Path:
        return self.root / "sweep.json"

    # -- small json helpers ---------------------------------------------
    @staticmethod
    def _write(path: Path, doc: dict | list) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(doc, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _read(path: Path, default):
        if not path.exists():
            return default
        return json.loads(path.read_text())

    # -- custody config (lifetime cap + ratchet delay) --------------------
    def save_custody(self, cap_lifetime: Decimal,
                     ratchet_delay_hours: Decimal) -> None:
        self._write(self.custody_file, {
            "cap_lifetime": str(cap_lifetime),
            "ratchet_delay_hours": str(ratchet_delay_hours),
        })

    def load_custody(self) -> dict:
        doc = self._read(self.custody_file, None)
        if doc is None:
            raise FileNotFoundError(str(self.custody_file))
        return doc

    # -- ratchet queue ----------------------------------------------------
    def pending_ratchets(self) -> list[dict]:
        return self._read(self.ratchet_file, [])

    def save_ratchets(self, pending: list[dict]) -> None:
        self._write(self.ratchet_file, pending)

    # -- clock high-water --------------------------------------------------
    def observe_clock(self, now_iso: str) -> bool:
        """Record the high-water mark; True if now is at/after it (sane),
        False if the clock has rolled back (anomaly — raises stay pending)."""
        doc = self._read(self.clock_file, {})
        high = doc.get("high_water")
        if high is None or now_iso >= high:
            self._write(self.clock_file, {"high_water": now_iso})
            return True
        return False

    # -- panic -------------------------------------------------------------
    def panic_record(self) -> dict | None:
        return self._read(self.panic_file, None)

    def write_panic(self, now_iso: str, reason: str) -> dict:
        rec = {"panicked_at": now_iso, "reason": reason}
        self._write(self.panic_file, rec)
        return rec

    def clear_panic(self) -> None:
        if self.panic_file.exists():
            self.panic_file.unlink()

    # -- ceremony ----------------------------------------------------------
    def rehearsal_record(self) -> dict | None:
        return self._read(self.ceremony_file, None)

    def write_rehearsal(self, now_iso: str, address: str) -> dict:
        rec = {"rehearsal_at": now_iso, "address": address}
        self._write(self.ceremony_file, rec)
        return rec

    # -- sweep phase -------------------------------------------------------
    def sweep_record(self) -> dict | None:
        return self._read(self.sweep_file, None)

    def save_sweep(self, rec: dict) -> None:
        self._write(self.sweep_file, rec)


def ceremony_state(wstate: StateDir, cstate: CustodyState) -> dict:
    """The three founding steps, derived from state — never cached flags."""
    rehearsal = cstate.rehearsal_record()
    steps = {
        "keygen": wstate.keystore.exists(),
        "backup_verified": wstate.backup_marker.exists(),
        "restore_rehearsal": rehearsal is not None,
    }
    return {**steps, "complete": all(steps.values()),
            "rehearsal_at": rehearsal["rehearsal_at"] if rehearsal else None}


def lifetime_spent(wstate: StateDir, now: datetime,
                   exclude_payment_id: str | None = None) -> Decimal:
    """All-time settled spend PLUS live outstanding authorizations — the
    lifetime cap's measure. Same reservation semantics as the inner daily
    cap (a settled record supersedes its payment_id's reservation) with no
    24h cutoff, and sweep authorizations excluded: sweep is the
    human-approved exit, not agent spend."""
    latest: dict[str, dict] = {}
    for rec in wstate.read_spends():
        if rec.get("sweep"):
            continue
        cur = latest.get(rec["payment_id"])
        if cur is None or cur["status"] != "settled":
            latest[rec["payment_id"]] = rec
    total = Decimal("0")
    for pid, rec in latest.items():
        if rec["status"] == "settled":
            total += Decimal(rec["amount"])
        elif (rec["status"] == "authorized" and pid != exclude_payment_id
              and rec.get("valid_before", 0) > now.timestamp()):
            total += Decimal(rec["amount"])
    return total
