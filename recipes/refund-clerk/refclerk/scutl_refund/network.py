"""Claims + settlement clients — the ONLY modules that talk to the outside.

Core never builds a URL and never parses raw HTTP; everything crosses
these boundaries as plain dicts. The claimant is ADVERSARIAL: every field
of a claim — the tx it names, the amount, the payer it asserts, the story
around them — is carried across faithfully, cheating and all, and core
believes NONE of it: the earnings ledger decides what happened, policy
decides what auto-pays, and the payout address comes from the settle
record, never from here.

Rev 1 is BENCH-FIRST: no live claim source or payout rail is blessed
(the support inbox composes inbox-triage in a future bindings rev; a
live payout is a wallet-recipe send). These classes are the shapes
blessed rails must fit; the mocks in smutbench/refund implement the same
surfaces. The manifest's contracts block is the source of truth:

  claimant.next()    -> {claim_id, text, claimed_tx, claimed_amount,
                         claimed_payer} | None      # all of it assertion
  settlement.payout(refund_id, address, amount)
                     -> {txid} | transient/permanent error
"""

from __future__ import annotations

import os


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry with the same refund id."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class ClaimsClient:
    """Live claim-queue surface. No blessed live default in rev 1:
    constructing one without an explicit binding refuses, so a bench rung
    must inject its mock and a future live rev must set the binding
    deliberately."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        self.base = base or os.environ.get("SCUTL_REFUND_CLAIMS")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no claims binding in rev 1: inject a mock (bench) or set "
                "SCUTL_REFUND_CLAIMS to a blessed source (future rev)")

    def next(self) -> dict | None:                 # pragma: no cover - stub
        raise PermanentError("live claims rail is not blessed in rev 1")


class SettlementClient:
    """Live payout surface — same rev-1 refusal. A live payout is a wallet
    send to the recorded payer; the clerk itself never holds a key."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        self.base = base or os.environ.get("SCUTL_REFUND_SETTLEMENT")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no settlement binding in rev 1: inject a mock (bench) or set "
                "SCUTL_REFUND_SETTLEMENT to a blessed rail (future rev)")

    def payout(self, refund_id: str, address: str, amount: str) -> dict:  # pragma: no cover
        raise PermanentError("live payout rail is not blessed in rev 1")
