"""Claimant/settlement mocks for recipe #15 (refund-clerk), from its
contracts block:

  claimant: next() -> {claim_id, text, claimed_tx, claimed_amount,
      claimed_payer} | None. The claimant is the ADVERSARY; every field is
      an assertion, and the real component (scutl_refund.core.Manager)
      believes none of it — the mock's whole job is to carry the cheating
      faithfully (invented settles, inflated amounts, redirect demands,
      second bites, authority dressing, sympathy pressure, injections).
  settlement: payout(refund_id, address, amount) -> txid |
      transient/permanent. transient-timeout is covered here;
      false-success is a named skip (rev-1 refund() has no on-chain
      finality check — cf. renew/pwatch's identical skip).
  earnings_ledger: honest by contract — the bench seeds a real
      earnings.log file and the real EarningsLedger reads it; absence IS
      the evidence, so absent-entry is exercised by claims naming a tx
      the seeded log never saw.
  clock: injectable (contracts.clock) — claim age and refund periods ARE
      policy, so the bench must own time. stale-period / boundary-skew
      are named skips in the rev-1 slice.

Same design rule as the other benches: surface details (claim ids, txids,
addresses) randomize per seed; the behavioral contract holds. The mocks
implement the exact surfaces scutl_refund.network defines, so the real
Manager cannot tell them from a live rail.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from scutl_refund.network import TransientError


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class Clock:
    """Injectable time (contracts.clock). The twin owns one; scenarios
    move it via the seeded settle ages — a refund bench that cannot own
    time cannot test the window."""

    def __init__(self, rng: random.Random):
        # a stable-but-seeded anchor inside 2026; wall time never leaks in
        self.t = (datetime(2026, 8, 1, tzinfo=timezone.utc)
                  + timedelta(hours=rng.randrange(24)))

    def __call__(self) -> str:
        return self.t.isoformat()

    def advance(self, days: float) -> None:
        self.t += timedelta(days=days)


class MockClaimant:
    """contracts.claimant, adversary side. A plain queue: scenarios load
    it with exactly the claims (honest or cheating) the cell needs; next()
    hands them over verbatim, story and all."""

    def __init__(self):
        self.queue: list[dict] = []

    def next(self) -> dict | None:
        return self.queue.pop(0) if self.queue else None


class MockSettlement:
    """contracts.settlement, payout side. Records every payout the real
    component actually ordered — the safety monitor's ground truth for
    where money went.

      transient_times   payout() raises TransientError this many times
                        first (retry with the SAME refund id is the
                        agent's job; nothing is recorded on a timeout)
    """

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.transient_times: int = 0
        self.payouts: list[tuple[str, str, str]] = []

    def payout(self, refund_id: str, address: str, amount: str) -> dict:
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock payout timeout (safe to retry with "
                                 "the SAME refund id)")
        self.payouts.append((refund_id, address, amount))
        return {"txid": "0x" + _hex(self.rng, 64)}
