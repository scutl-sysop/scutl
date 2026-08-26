"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/refund, dir mode 0700):
  policy.json           the outbound bounds: window_days, auto_max, period_cap,
                        period_days, the policy anchor timestamp, and the path
                        to the merchant's earnings.log — written only by the
                        human-approved configure admin op                 (0600)
  claims/               one file per fetched claim id: the claimant's
                        assertions VERBATIM (text, claimed tx, amount, payer),
                        plus clerk-side lifecycle marks (exception_granted,
                        denied, settled). The assertions are never edited;
                        the marks are appended fields.
  refunds.log           append-only JSONL: one record per settled refund and
                        per refused attempt (invented settle, over-amount,
                        double-refund, post-decommission); the period payout
                        counter, the per-settle refunded total, and the
                        idempotency question always derive from it
  parked.json           present while a claim is parked for a human exception:
                        the claim id, the evidence, and the policy line it
                        crossed, so status shows the human exactly what they
                        are being asked to approve
  approvals/            consumable human-approval token files. The exception
                        and deny tokens are SCOPED: their contents pin the
                        claim id, and they are void for any other claim
  tombstone.marker      written by decommission(); refunds refuse thereafter —
                        status does NOT (reading the record is never gated)

earnings.log is the MERCHANT's ledger (paid-service recipe) and is
read-only to the clerk: a component that can edit its own evidence
proves nothing. The clerk holds no key material; the live payout rail is
the wallet recipe when that binding lands. In rev 1 the mock settles.

Time: refund periods are indexed from the policy anchor (period_of
below); claim age derives from the settle's own timestamp. The clock is
injectable (contracts.clock).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class Tombstoned(Exception):
    """Decommission marker present; refunds refuse. status does not."""


class NotConfigured(Exception):
    """No policy.json yet; run 'refclerk admin configure' first."""


class UnknownClaim(Exception):
    """No fetched claim with this id — call claim first."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def period_of(anchor_iso: str, period_days: int, now_iso: str) -> int:
    """Refund-period index since the policy anchor."""
    anchor = datetime.fromisoformat(anchor_iso)
    now = datetime.fromisoformat(now_iso)
    delta = now - anchor
    if delta.total_seconds() < 0:
        return 0
    return int(delta.days // period_days)


def age_days(settled_at: str, now_iso: str) -> int:
    """Whole days between a settle and now (contracts.clock age_of)."""
    settled = datetime.fromisoformat(settled_at)
    now = datetime.fromisoformat(now_iso)
    delta = now - settled
    if delta.total_seconds() < 0:
        return 0
    return delta.days


class EarningsLedger:
    """Read-only view of the merchant's earnings.log (paid-service recipe):
    append-only JSONL, one record per settled sale. Absence of a tx here IS
    the evidence that a claimed charge never happened — the ledger records
    every settle (contracts.earnings_ledger)."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).expanduser()

    def lookup(self, tx: str) -> dict | None:
        if not self.path.exists():
            return None
        for line in self.path.read_text().splitlines():
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("settle_tx") == tx:
                return {"settle_tx": rec["settle_tx"],
                        "settled_usdc": str(rec["settled_usdc"]),
                        "payer_address": rec["payer_address"],
                        "settled_at": rec["settled_at"]}
        return None


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_REFUND_STATE")
            or Path.home() / ".scutl" / "refund"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)
        (self.root / "claims").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def policy_file(self) -> Path:
        return self.root / "policy.json"

    @property
    def claims(self) -> Path:
        return self.root / "claims"

    @property
    def refunds_log(self) -> Path:
        return self.root / "refunds.log"

    @property
    def parked_file(self) -> Path:
        return self.root / "parked.json"

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
                json.loads(self.tombstone_marker.read_text())["decommissioned_at"])

    # -- policy (integrity-critical: the outbound bounds live here) ------
    def load_policy(self) -> dict:
        if not self.policy_file.exists():
            raise NotConfigured(str(self.policy_file))
        return json.loads(self.policy_file.read_text())

    def save_policy(self, policy: dict) -> None:
        self.init()
        fd = os.open(self.policy_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(policy, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- claims (the claimant's assertions, verbatim + lifecycle marks) --
    def save_claim(self, claim: dict) -> None:
        self.init()
        path = self.claims / f"{claim['claim_id']}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(claim, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def load_claim(self, claim_id: str) -> dict:
        path = self.claims / f"{claim_id}.json"
        if not path.exists():
            raise UnknownClaim(claim_id)
        return json.loads(path.read_text())

    def mark_claim(self, claim_id: str, **marks) -> dict:
        claim = self.load_claim(claim_id)
        claim.update(marks)
        self.save_claim(claim)
        return claim

    def open_claims(self) -> list[dict]:
        out = []
        if not self.claims.exists():
            return out
        for path in sorted(self.claims.glob("*.json")):
            claim = json.loads(path.read_text())
            if not claim.get("settled") and not claim.get("denied"):
                out.append(claim)
        return out

    # -- parked claim (waiting on a human exception) ---------------------
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

    # -- refunds log (append-only; every counter derives from it) --------
    def append_event(self, record: dict) -> None:
        self.init()
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.refunds_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.refunds_log.exists():
            return []
        return [json.loads(l) for l in self.refunds_log.read_text().splitlines() if l]

    def settled_refunds(self) -> list[dict]:
        return [r for r in self.read_events() if r["event"] == "refunded"]

    def refunded_for_settle(self, settle_tx: str) -> Decimal:
        """Total already paid back against one settle — the per-settle bound.
        Split claims count against the same settle by construction."""
        total = Decimal("0")
        for r in self.settled_refunds():
            if r.get("settle_tx") == settle_tx:
                total += Decimal(str(r["amount_usdc"]))
        return total

    def refunds_in_period(self, anchor: str, period_id: int) -> list[dict]:
        return [r for r in self.settled_refunds()
                if r.get("policy_anchor") == anchor
                and r.get("period_id") == period_id]

    def refunded_in_period(self, anchor: str, period_id: int) -> Decimal:
        total = Decimal("0")
        for r in self.refunds_in_period(anchor, period_id):
            total += Decimal(str(r["amount_usdc"]))
        return total

    def already_refunded(self, refund_id: str) -> dict | None:
        """Idempotency: a retried refund with the same refund id returns the
        original payout rather than paying twice."""
        for r in self.settled_refunds():
            if r.get("refund_id") == refund_id:
                return r
        return None
