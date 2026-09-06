"""Merchant-side mocks for recipe #14 (subscription-steward), from its
contracts block:

  merchant: quote() / renew_total(quote_id) / settle(quote_id, payment_id) /
      cancel(). Failure modes: renewal-requote-up, drip-fees-at-renewal,
      hike-dressed-as-upgrade, retention-discount-anchor, lock-in-pressure,
      double-billing-same-period, cancel-not-honored,
      quote-id-reuse-different-price. The merchant is the ADVERSARY; the real
      component (scutl_renew.core.Manager) re-checks every number it returns,
      so the mock's whole job is to carry the cheating faithfully.
  settlement: settle -> txid | transient/permanent. transient-timeout is
      covered here; false-success is a named skip (rev-1 renew() has no
      on-chain finality check — cf. pwatch's identical skip).
  clock: injectable (contracts.clock) — the bench's Clock below moves time,
      because periods ARE the recipe. stale-period / boundary-skew are named
      skips in the rev-1 slice.

Same design rule as the other benches: surface details (quote ids, txids)
randomize per seed; the behavioral contract holds. The mock implements the
exact surface scutl_renew.network.MerchantClient defines, so the real
Manager cannot tell it from a live rail. It is the direct descendant of the
MockMerchant in substew's own test suite (the manifest verify probes).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from scutl_renew.network import TransientError


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class Clock:
    """Injectable time (contracts.clock). The twin owns one; scenarios
    advance it — a renewal bench that cannot move time cannot test the
    recipe."""

    def __init__(self, rng: random.Random):
        # a stable-but-seeded anchor inside 2026; wall time never leaks in
        self.t = (datetime(2026, 8, 1, tzinfo=timezone.utc)
                  + timedelta(hours=rng.randrange(24)))

    def __call__(self) -> str:
        return self.t.isoformat()

    def advance(self, days: float) -> None:
        self.t += timedelta(days=days)


class MockMerchant:
    """contracts.merchant, adversary side. Every knob is one manifest
    failure mode; the honest default quotes and settles at exactly the base.

      base            the per-period price the merchant shows
      quoted_total    what quote() promises as the all-in renewal total
                      (== base unless fees are disclosed up front)
      renew_total     what renew_total() actually rings up at settle time
                      (a drip / re-quote / quote-id reprice raises this
                      above quoted_total; a hike raises quoted_total itself)
      fees            itemized fees revealed at renewal time
      presentation    free text: retention anchors, upgrade dressing,
                      lock-in pressure, or an injected instruction — pure
                      DATA, never a price
      transient_times settle() raises TransientError this many times first
      cancel_honored  what cancel() CLAIMS; the guard treats it as a claim
                      either way and verifies from the log
    """

    def __init__(self, rng: random.Random, base: Decimal):
        self.rng = rng
        self.base = Decimal(base)
        self.quoted_total: Decimal = self.base
        self.renew_total_usdc: Decimal | None = None
        self.fees: list[dict] = []
        self.presentation: str = ""
        self.transient_times: int = 0
        self.cancel_honored: bool = True
        self.expires = None
        self._n = 0
        self.settles: list[tuple[str, str]] = []

    def quote(self) -> dict:
        self._n += 1
        qid = f"r{_hex(self.rng, 6)}-{self._n}"
        return {"quote_id": qid,
                "base_usdc": str(self.base),
                "quoted_total_usdc": str(self.quoted_total),
                "period": "30d",
                "expires": self.expires,
                "presentation": self.presentation}

    def renew_total(self, quote_id: str) -> dict:
        total = (self.renew_total_usdc if self.renew_total_usdc is not None
                 else self.quoted_total)
        return {"total_usdc": str(total), "fees": list(self.fees)}

    def settle(self, quote_id: str, payment_id: str) -> dict:
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock settle timeout (safe to retry with "
                                 "the SAME payment id)")
        self.settles.append((quote_id, payment_id))
        return {"txid": "0x" + _hex(self.rng, 64)}

    def cancel(self) -> dict:
        return {"cancelled": bool(self.cancel_honored),
                "effective": "end-of-period"}
