"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/renew, dir mode 0700):
  consent.json          the line: service, agreed_price, period_days,
                        cap_per_renewal, cap_period, max_fees_pct, and the
                        consent anchor timestamp — written only by the
                        human-approved consent/re-consent admin ops    (0600)
  quotes/               one file per live quote id: the base and total the
                        merchant SHOWED at quote time. renew() compares the
                        settle-time total against this — a merchant that
                        settles above what it quoted moved uphill.
  billing.log           append-only JSONL: one record per settled renewal and
                        per refused attempt (hike, drip, double-billing,
                        post-cancel charge); the period spend counter, the
                        period-already-settled question, and cancel
                        verification always derive from it
  parked.json           present while a renewal is parked for re-consent:
                        the quote id and the totals, so status can show the
                        human exactly what they are being asked to approve
  approvals/            consumable human-approval token files. The re-consent
                        token is SCOPED: its contents pin the NEW price, and
                        it is void for any other price
  cancel.json           written by the cancel admin op: the merchant's claim
                        AND the effective date. renew refuses thereafter;
                        verification (billing actually stopped) derives from
                        the log, not from this file
  tombstone.marker      written by revoke(); renew refuses thereafter —
                        status does NOT (reading the record is never gated)

There is no secret in this state dir: like the price-watcher, the steward
never holds key material. Settlement borrows the wallet recipe's signer
when the live x402-recurring binding lands; in rev 1 the mock settles.

Time: periods are indexed from the consent anchor (period_of below). The
clock is injectable (contracts.clock) because the time axis is the recipe.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class Tombstoned(Exception):
    """Revoke marker present; renew refuses. status does not."""


class NotConfigured(Exception):
    """No consent.json yet; run 'substew admin consent' first."""


class UnknownQuote(Exception):
    """No live quote for this id — never seen, or already settled/expired."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def period_of(consented_at: str, period_days: int, now_iso: str) -> int:
    """Period index since the consent anchor. Re-consent moves the anchor,
    which is correct: the new agreement starts a new period sequence."""
    anchor = datetime.fromisoformat(consented_at)
    now = datetime.fromisoformat(now_iso)
    delta = now - anchor
    if delta.total_seconds() < 0:
        return 0
    return int(delta.days // period_days)


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_RENEW_STATE")
            or Path.home() / ".scutl" / "renew"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)
        (self.root / "quotes").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def consent_file(self) -> Path:
        return self.root / "consent.json"

    @property
    def quotes(self) -> Path:
        return self.root / "quotes"

    @property
    def billing_log(self) -> Path:
        return self.root / "billing.log"

    @property
    def parked_file(self) -> Path:
        return self.root / "parked.json"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    @property
    def cancel_file(self) -> Path:
        return self.root / "cancel.json"

    @property
    def tombstone_marker(self) -> Path:
        return self.root / "tombstone.marker"

    # -- guards --------------------------------------------------------
    def check_not_tombstoned(self) -> None:
        if self.tombstone_marker.exists():
            raise Tombstoned(
                json.loads(self.tombstone_marker.read_text())["revoked_at"])

    # -- consent (integrity-critical: the line lives here) ---------------
    def load_consent(self) -> dict:
        if not self.consent_file.exists():
            raise NotConfigured(str(self.consent_file))
        return json.loads(self.consent_file.read_text())

    def save_consent(self, consent: dict) -> None:
        self.init()
        fd = os.open(self.consent_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(consent, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- quotes (what the merchant SHOWED; renew compares against it) ----
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

    # -- parked renewal (waiting on re-consent) --------------------------
    def park(self, record: dict) -> None:
        self.init()
        fd = os.open(self.parked_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(record, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def parked(self) -> dict | None:
        if not self.parked_file.exists():
            return None
        return json.loads(self.parked_file.read_text())

    def unpark(self) -> None:
        if self.parked_file.exists():
            self.parked_file.unlink()

    # -- cancel record ---------------------------------------------------
    def save_cancel(self, record: dict) -> None:
        self.init()
        fd = os.open(self.cancel_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(record, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def cancel_record(self) -> dict | None:
        if not self.cancel_file.exists():
            return None
        return json.loads(self.cancel_file.read_text())

    # -- billing log (append-only; every counter derives from it) --------
    def append_event(self, record: dict) -> None:
        self.init()
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.billing_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.billing_log.exists():
            return []
        return [json.loads(l) for l in self.billing_log.read_text().splitlines() if l]

    def settled_renewals(self) -> list[dict]:
        return [r for r in self.read_events() if r["event"] == "renewed"]

    def settled_in_period(self, consent_anchor: str, period_id: int) -> list[dict]:
        """Settles recorded against THIS consent's period index. Records pin
        the anchor so a re-consent (new anchor) starts a clean sequence."""
        return [r for r in self.settled_renewals()
                if r.get("consent_anchor") == consent_anchor
                and r.get("period_id") == period_id]

    def spent_in_period(self, consent_anchor: str, period_id: int) -> Decimal:
        total = Decimal("0")
        for r in self.settled_in_period(consent_anchor, period_id):
            total += Decimal(str(r["total_usdc"]))
        return total

    def already_settled(self, payment_id: str) -> dict | None:
        """Idempotency: a retried renew with the same payment id returns the
        original settle rather than paying twice."""
        for r in self.settled_renewals():
            if r.get("payment_id") == payment_id:
                return r
        return None

    def charges_after(self, cancelled_at: str) -> list[dict]:
        """Settles and attempts after the cancel record — the evidence that
        decides whether a cancel is verified."""
        return [r for r in self.read_events()
                if r["ts"] > cancelled_at
                and r["event"] in ("renewed", "refused")]
