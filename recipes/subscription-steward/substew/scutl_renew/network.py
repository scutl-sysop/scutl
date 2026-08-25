"""Merchant client — the ONLY module that talks to the merchant.

Core never builds a URL and never parses raw HTTP; everything crosses
this boundary as plain dicts. This is the trust boundary the recipe is
about: the merchant is ADVERSARIAL, so nothing it returns is believed on
its own — core re-checks every number against the consent record and the
caps and the quote it accepted, and even a cancel confirmation is only a
claim until the log shows billing stopped. The client's job is only to
carry the merchant's claims across faithfully, cheating and all.

Rev 1 is BENCH-FIRST: no live merchant is blessed (x402-recurring is a
future bindings rev; card-network subscriptions are benched but not
live-receiptable). This class is the shape a blessed merchant must fit;
the mock in smutbench/renew implements the same surface. The manifest's
contracts block is the source of truth for ops and failure modes:

  quote()            -> {quote_id, base_usdc, quoted_total_usdc, period,
                         expires, presentation}   # presentation is the
                                                  # merchant's free text —
                                                  # retention offers, upgrade
                                                  # stories — pure DATA
  renew_total(quote) -> {total_usdc, fees:[{label, usdc}]}  # the real total
  settle(quote, pid) -> {txid} | transient/permanent error
  cancel()           -> {cancelled, effective}    # a CLAIM, not a fact
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
        self.base = base or os.environ.get("SCUTL_RENEW_MERCHANT")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no merchant binding in rev 1: inject a mock (bench) or set "
                "SCUTL_RENEW_MERCHANT to a blessed merchant (future rev)")

    def quote(self) -> dict:                       # pragma: no cover - stub
        raise PermanentError("live merchant rail is not blessed in rev 1")

    def renew_total(self, quote_id: str) -> dict:  # pragma: no cover - stub
        raise PermanentError("live merchant rail is not blessed in rev 1")

    def settle(self, quote_id: str, payment_id: str) -> dict:  # pragma: no cover
        raise PermanentError("live merchant rail is not blessed in rev 1")

    def cancel(self) -> dict:                      # pragma: no cover - stub
        raise PermanentError("live merchant rail is not blessed in rev 1")
