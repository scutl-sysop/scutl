"""Merchant-side mocks for recipe #25 (price-watch-and-buy), from its
contracts block:

  merchant: quote(item) / checkout(quote_id) / settle(quote_id, payment_id).
      Failure modes: personalized-pricing, drip-fees-at-checkout,
      buy-time-requote-up, fake-anchor-discount, phantom-scarcity,
      quote-id-reuse-different-price. The merchant is the ADVERSARY; the
      real component (scutl_pwatch.core.Manager) re-checks every number it
      returns, so the mock's whole job is to carry the cheating faithfully.
  settlement: settle -> txhash | transient/permanent. Modes: transient-timeout
      (covered here), false-success (no operator-facing on-chain check in the
      rev-1 execute slice — a named skip, never silence).
  clock: now() / price_feed. stale-quote is a named skip for the same reason
      (rev-1 buy() holds the line on price, not freshness).

Same design rule as the wallet/pserv mocks: surface details (quote ids,
txids) randomize per seed; the behavioral contract holds. The mock
implements the exact surface scutl_pwatch.network.MerchantClient defines,
so the real Manager cannot tell it from a live rail.
"""

from __future__ import annotations

import random
from decimal import Decimal

from scutl_pwatch.network import TransientError


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class MockMerchant:
    """contracts.merchant, adversary side. Every knob is one manifest
    failure mode; the honest default quotes and settles at exactly the
    sticker.

      sticker         the number the merchant shows (personalized/anchored)
      quoted_total    what quote() promises as the all-in total (== sticker
                      unless fees are disclosed up front)
      checkout_total  what checkout() actually rings up (drip / re-quote /
                      quote-id reprice raise this above quoted_total)
      fees            itemized fees revealed at checkout
      presentation    free text: anchors, scarcity, 'price for you', or an
                      injected instruction — pure DATA, never a price
      transient_times settle() raises TransientError this many times first
    """

    def __init__(self, rng: random.Random, sticker: Decimal):
        self.rng = rng
        self.sticker = Decimal(sticker)
        self.quoted_total: Decimal = self.sticker
        self.checkout_total: Decimal | None = None
        self.fees: list[dict] = []
        self.presentation: str = ""
        self.transient_times: int = 0
        self.expires = None
        self._n = 0
        self.settles: list[tuple[str, str]] = []

    def quote(self, item: str) -> dict:
        self._n += 1
        qid = f"q{_hex(self.rng, 6)}-{self._n}"
        return {"quote_id": qid,
                "sticker_usdc": str(self.sticker),
                "quoted_total_usdc": str(self.quoted_total),
                "expires": self.expires,
                "presentation": self.presentation}

    def checkout(self, quote_id: str) -> dict:
        total = (self.checkout_total if self.checkout_total is not None
                 else self.quoted_total)
        return {"total_usdc": str(total), "fees": list(self.fees)}

    def settle(self, quote_id: str, payment_id: str) -> dict:
        if self.transient_times > 0:
            self.transient_times -= 1
            raise TransientError("mock settle timeout (safe to retry with "
                                 "the SAME payment id)")
        self.settles.append((quote_id, payment_id))
        return {"txid": "0x" + _hex(self.rng, 64)}
