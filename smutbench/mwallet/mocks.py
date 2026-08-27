"""Mocks for recipe #1 (mwallet), from its contracts block.

  facilitator / chain: REUSED from smutbench.mocks (MockFacilitator,
      MockChain) — the mwallet contracts for both are word-for-word the
      sepolia wallet's (verify/settle, balance/tx_status) and the inner
      signer is the same code, so the same mocks hold. fee-spike is a
      named skip: the wallet is gasless by construction (every spend is
      an EIP-3009 authorization the counterparty submits and pays gas
      for), so a gas-fee spike has no reachable surface in this recipe.
  counterparty: the adversarial surface — offer(url) -> {amount, payTo,
      resource}. The offer's free-text `note` is the ONLY channel that
      carries counterparty prose (every other tool result is typed JSON
      from real code); the manifest's counterparty failure modes all ride
      in it or in the offer fields themselves.
  onramp: human-out-of-band by contract — the agent only ever observes
      the chain. The bench models a deposit as a scheduled balance drip
      (or its absence / a partial amount), exactly what mw_status can see.
  clock: injectable (contracts.clock). The ratchet cooling-off delay is
      the recipe's time axis; the bench owns time, so a 24h delay is one
      advance() tick and a clock rollback is a plain assignment. The
      clock starts at REAL now: the inner signer stamps spend records
      with the wall clock (documented rev-1 seam), so bench time must
      begin in the same neighborhood and diverge only by explicit moves.

Same design rule as every bench: surface details (addresses, prices,
resource slugs) randomize per seed; the behavioral contract holds.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..mocks import MockChain, MockFacilitator  # noqa: F401  (re-exported)


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class FakeClock:
    """contracts.clock: now() -> t. Starts at real now (inner-signer
    seam, see module docstring); scenarios move it forward with
    advance() or roll it back by assigning .now."""

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class MockCounterparty:
    """contracts.counterparty: offer(url) -> {amount, payTo, resource}.

    Offers are set by the twin/scenarios; serve() hands the CURRENT offer
    over verbatim, note and all. Re-quote hooks (facilitator/changed-price
    rides through the counterparty's menu) fire after an offer is served,
    mirroring the sepolia merchant."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.offers: dict[str, dict] = {}     # url -> offer dict
        self.serves = 0

    def set_offer(self, url: str, amount: str, pay_to: str,
                  resource: str, note: str = "", body: str = "") -> None:
        self.offers[url] = {"amount": str(amount), "pay_to": pay_to,
                            "resource": resource, "note": note,
                            "body": body or f"resource payload {resource}"}

    def serve(self, url: str) -> dict | None:
        offer = self.offers.get(url)
        if offer is not None:
            self.serves += 1
        return offer
