"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/wallet, dir mode 0700):
  keystore.json   eth keystore v3, encrypted with the KEK          (0600)
  kek             key-encryption passphrase, random, hex           (0600)
  caps.json       {"cap_per_tx": "...", "cap_daily": "..."} USDC decimals
  spend.log       append-only JSONL, one record per pay() attempt
  backup.marker   written by backup-verify: sha256 of keystore.json
  tombstone.json  written by revoke(); its existence disables everything
  approvals/      consumable human-approval token files, see approvals.py

The keystore/KEK split makes the human's backup artifact (keystore.json)
useless on its own; backup instructions tell the human to store the two
files separately. Neither file's contents ever pass through a tool result.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


class Revoked(Exception):
    """Wallet has a tombstone; all operations refuse."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root or os.environ.get("SCUTL_STATE") or Path.home() / ".scutl" / "wallet"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def keystore(self) -> Path:
        return self.root / "keystore.json"

    @property
    def kek(self) -> Path:
        return self.root / "kek"

    @property
    def caps_file(self) -> Path:
        return self.root / "caps.json"

    @property
    def spend_log(self) -> Path:
        return self.root / "spend.log"

    @property
    def backup_marker(self) -> Path:
        return self.root / "backup.marker"

    @property
    def tombstone(self) -> Path:
        return self.root / "tombstone.json"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    # -- guards --------------------------------------------------------
    def check_not_revoked(self) -> None:
        if self.tombstone.exists():
            raise Revoked(json.loads(self.tombstone.read_text())["address"])

    def write_secret(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- caps ----------------------------------------------------------
    def load_caps(self) -> dict[str, Decimal]:
        raw = json.loads(self.caps_file.read_text())
        return {k: Decimal(v) for k, v in raw.items()}

    def save_caps(self, cap_per_tx: Decimal, cap_daily: Decimal) -> None:
        self.caps_file.write_text(
            json.dumps({"cap_per_tx": str(cap_per_tx), "cap_daily": str(cap_daily)})
        )

    # -- cap serialization (cst-8ih.6) -----------------------------------
    @contextmanager
    def cap_lock(self):
        """Exclusive lock held across cap-check + reservation append, so
        concurrent authorize()/pay() calls cannot both read the same stale
        exposure and each pass the daily cap. Never hold it across network
        calls."""
        fd = os.open(self.root / "cap.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # -- spend log (append-only; counters always derive from it) --------
    def append_spend(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.spend_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_spends(self) -> list[dict]:
        if not self.spend_log.exists():
            return []
        return [json.loads(l) for l in self.spend_log.read_text().splitlines() if l]

    def settled_by_payment_id(self, payment_id: str) -> dict | None:
        for rec in self.read_spends():
            if rec["payment_id"] == payment_id and rec["status"] == "settled":
                return rec
        return None

    def spent_last_24h(self, now: datetime | None = None) -> Decimal:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        total = Decimal("0")
        for rec in self.read_spends():
            if rec["status"] != "settled":
                continue
            ts = datetime.fromisoformat(rec["ts"])
            if ts >= cutoff:
                total += Decimal(rec["amount"])
        return total

    def cap_exposure(self, now: datetime | None = None,
                     exclude_payment_id: str | None = None) -> Decimal:
        """What the daily cap must be measured against (cst-8ih.6): settled
        spend in the last 24h PLUS outstanding authorizations — signed but
        not yet recorded settled — that the merchant could still settle
        (valid_before in the future). A settled record supersedes its
        payment_id's reservation; among reservations for one payment_id the
        latest wins (re-authorizing re-signs the same nonce, so at most one
        can settle on-chain). exclude_payment_id keeps a replayed authorize
        of the SAME payment from double-counting its own reservation."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        latest: dict[str, dict] = {}
        for rec in self.read_spends():
            cur = latest.get(rec["payment_id"])
            if cur is None or cur["status"] != "settled":
                latest[rec["payment_id"]] = rec
        total = Decimal("0")
        for pid, rec in latest.items():
            if rec["status"] == "settled":
                if datetime.fromisoformat(rec["ts"]) >= cutoff:
                    total += Decimal(rec["amount"])
            elif (rec["status"] == "authorized" and pid != exclude_payment_id
                  and rec["valid_before"] > now.timestamp()):
                total += Decimal(rec["amount"])
        return total
