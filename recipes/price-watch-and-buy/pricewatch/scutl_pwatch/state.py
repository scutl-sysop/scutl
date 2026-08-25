"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/pwatch, dir mode 0700):
  target.json           the line and the caps: item, target_price,
                        cap_per_buy, cap_daily, max_fees_pct — written only
                        by the human-approved set-target admin op    (0600)
  quotes/               one file per live quote id: the sticker and the
                        quoted total the merchant SHOWED at quote time. buy()
                        compares the checkout total against this — a merchant
                        that settles above what it quoted moved uphill.
  spend.log             append-only JSONL: one record per settled buy and per
                        refused-uphill/over-ceiling attempt; the daily spend
                        counter and the "have we bought for this target yet"
                        question always derive from it
  approvals/            consumable human-approval token files. first-buy.token
                        is SCOPED: its contents pin (item, target_price), and
                        it is void the moment either changes
  tombstone.marker      written by revoke(); buy refuses thereafter — status
                        does NOT (reading the record is never gated)

There is no secret in this state dir: unlike the wallet or capp, the
price-watcher never holds key material. Settlement borrows the wallet
recipe's signer when the live x402-merchant binding lands; in rev 1 the
mock merchant settles.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class Tombstoned(Exception):
    """Revoke marker present; buy refuses. status does not."""


class NotConfigured(Exception):
    """No target.json yet; run 'pricewatch admin set-target' first."""


class UnknownQuote(Exception):
    """No live quote for this id — never seen, or already settled/expired."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_PWATCH_STATE")
            or Path.home() / ".scutl" / "pwatch"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)
        (self.root / "quotes").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def target_file(self) -> Path:
        return self.root / "target.json"

    @property
    def quotes(self) -> Path:
        return self.root / "quotes"

    @property
    def spend_log(self) -> Path:
        return self.root / "spend.log"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    @property
    def tombstone_marker(self) -> Path:
        return self.root / "tombstone.marker"

    # -- guards --------------------------------------------------------
    def check_not_tombstoned(self) -> None:
        if self.tombstone_marker.exists():
            raise Tombstoned(
                json.loads(self.tombstone_marker.read_text())["revoked_at"])

    # -- target + caps (integrity-critical: the line lives here) --------
    def load_target(self) -> dict:
        if not self.target_file.exists():
            raise NotConfigured(str(self.target_file))
        return json.loads(self.target_file.read_text())

    def save_target(self, target: dict) -> None:
        self.init()
        fd = os.open(self.target_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(target, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- quotes (what the merchant SHOWED; buy compares against it) ------
    def save_quote(self, quote: dict) -> None:
        self.init()
        path = self.quotes / f"{quote['quote_id']}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(quote).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def load_quote(self, quote_id: str) -> dict:
        path = self.quotes / f"{quote_id}.json"
        if not path.exists():
            raise UnknownQuote(quote_id)
        return json.loads(path.read_text())

    def retire_quote(self, quote_id: str) -> None:
        """A settled quote id cannot be reused (one settle per quote)."""
        path = self.quotes / f"{quote_id}.json"
        if path.exists():
            path.unlink()

    # -- spend log (append-only; daily counter + first-buy derive from it)
    def append_event(self, record: dict) -> None:
        self.init()
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.spend_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.spend_log.exists():
            return []
        return [json.loads(l) for l in self.spend_log.read_text().splitlines() if l]

    def settled_buys(self) -> list[dict]:
        return [r for r in self.read_events() if r["event"] == "bought"]

    def spent_since(self, since_iso: str) -> Decimal:
        """USDC settled at or after `since_iso` — the rolling daily total."""
        total = Decimal("0")
        for r in self.settled_buys():
            if r["ts"] >= since_iso:
                total += Decimal(str(r["total_usdc"]))
        return total

    def has_bought_for(self, item: str, target_price: str) -> bool:
        """Has a buy ever settled for THIS exact (item, target)? A target
        change writes a new target_price, so the first-buy gate re-arms."""
        return any(r["item"] == item and r["target_price"] == str(target_price)
                   for r in self.settled_buys())

    def already_settled(self, payment_id: str) -> dict | None:
        """Idempotency: a retried buy with the same payment id returns the
        original settle rather than paying twice."""
        for r in self.settled_buys():
            if r.get("payment_id") == payment_id:
                return r
        return None
