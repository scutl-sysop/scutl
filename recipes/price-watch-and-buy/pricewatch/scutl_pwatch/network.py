"""Merchant client — the ONLY module that talks to the merchant.

Core never builds a URL and never parses raw HTTP; everything crosses
this boundary as plain dicts. This is also the trust boundary the recipe
is about: the merchant is ADVERSARIAL, so nothing it returns is believed
on its own — core re-checks every number against the target and the caps
and the quote it accepted. The client's job is only to carry the
merchant's claims across faithfully, cheating and all.

Rev 1 is BENCH-FIRST: no live merchant is blessed (x402-merchant is a
future bindings rev; card-network and closed-retail checkout are benched
but not live-receiptable). This class is the shape a blessed merchant
must fit; the mock in smutbench/pwatch implements the same surface. The
manifest's contracts block is the source of truth for ops and failure
modes:

  quote(item)      -> {quote_id, sticker_usdc, quoted_total_usdc, expires,
                       presentation}      # presentation is the merchant's
                                          # free text — anchors, scarcity,
                                          # "price for you" — pure DATA
  checkout(quote)  -> {total_usdc, fees:[{label, usdc}]}   # the real total
  settle(quote,pid)-> {txid} | transient/permanent error
"""

from __future__ import annotations


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry with the same payment id."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class MerchantClient:
    """Live merchant surface. No blessed live default in rev 1: constructing
    one without an explicit binding refuses, so a bench rung must inject its
    mock and a future live rev must set the binding deliberately."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        import os
        self.base = base or os.environ.get("SCUTL_PWATCH_MERCHANT")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no merchant binding in rev 1: inject a mock (bench) or set "
                "SCUTL_PWATCH_MERCHANT to a blessed merchant (future rev)")

    def quote(self, item: str) -> dict:            # pragma: no cover - stub
        raise PermanentError("live merchant rail is not blessed in rev 1")

    def checkout(self, quote_id: str) -> dict:     # pragma: no cover - stub
        raise PermanentError("live merchant rail is not blessed in rev 1")

    def settle(self, quote_id: str, payment_id: str) -> dict:  # pragma: no cover
        raise PermanentError("live merchant rail is not blessed in rev 1")
