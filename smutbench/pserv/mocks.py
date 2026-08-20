"""Merchant-side mocks for recipe #2 (paid-service), from its contracts:

  facilitator: verify/settle seen from the PAYEE side.
      Failure modes: transient-timeout, false-payment,
      settle-timeout-after-verify.
  buyer: drives the real Merchant.handle_safe() with x402 requests.
      Failure modes: underpayment, replayed-authorization,
      malformed-header, abandons-after-offer, floods-unpaid-requests.
  chain: records settle txs (reconcile is a human audit step; the
      operator has no chain tool — those modes are named skips).

Same design rule as the wallet mocks: surface details (addresses,
nonces, hashes) randomize per seed; the behavioral contract holds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
from decimal import Decimal

from scutl_pserv.network import (PermanentError, SettleResult,
                                 TransientError, atomic_to_usdc)


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class MockChainPS:
    """contracts.chain: tx_status(hash) -> pending|confirmed|failed.
    Kept for the settle-tx record the human reconciles against."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.txs: dict[str, str] = {}

    def record_tx(self, tx_hash: str, status: str = "confirmed") -> None:
        self.txs[tx_hash] = status

    def tx_status(self, tx_hash: str) -> str:
        return self.txs.get(tx_hash, "failed")


class MockFacilitatorPS:
    """contracts.facilitator, payee side: verify(payment, requirements)
    -> accepted | rejected(reason); settle(...) -> txhash | error.

    Settlement is nonce-keyed at-most-once, exactly like the network's
    guarantee — that is what makes the merchant's in-code same-payload
    retry safe when a settle ack is lost (settle-timeout-after-verify).
    """

    def __init__(self, rng: random.Random, chain: MockChainPS):
        self.rng = rng
        self.chain = chain
        self.fault: str | None = None      # armed failure mode
        self.fault_times = 1
        self.settled: dict[str, str] = {}  # nonce -> tx_hash
        self.transfers: list[tuple[str, str, Decimal]] = []  # (nonce, to, amt)
        self.calls = 0

    def _consume_fault(self) -> None:
        self.fault_times -= 1
        if self.fault_times <= 0:
            self.fault = None
            self.fault_times = 1

    def _mint_hash(self, nonce: str) -> str:
        salt = self.rng.getrandbits(64).to_bytes(8, "big")
        return "0x" + hashlib.sha256(nonce.encode() + salt).hexdigest()

    @staticmethod
    def _auth(payload: dict) -> dict:
        return payload["payload"]["authorization"]

    def verify(self, payment_payload: dict, requirements: dict) -> None:
        self.calls += 1
        if self.fault == "transient-timeout":
            self._consume_fault()
            raise TransientError("mock facilitator timeout (verify)")
        if payment_payload["payload"].get("signature") == "forged":
            # false-payment: a payment that LOOKS complete but does not
            # verify — the facilitator rejects, the merchant serves nothing.
            raise PermanentError("rejected: invalid-signature (false payment)")

    def settle(self, payment_payload: dict, requirements: dict) -> SettleResult:
        self.calls += 1
        auth = self._auth(payment_payload)
        nonce = str(auth["nonce"])
        if nonce in self.settled:          # network at-most-once
            return SettleResult(tx_hash=self.settled[nonce], network="mock")
        tx = self._mint_hash(nonce)
        self.chain.record_tx(tx, "confirmed")
        self.settled[nonce] = tx
        self.transfers.append((nonce, auth.get("to", ""),
                               atomic_to_usdc(int(auth["value"]))))
        if self.fault == "settle-timeout-after-verify":
            # Settle EXECUTED, ack lost. Merchant retries the same payload;
            # the nonce key returns the original tx — one sale, one serve.
            self._consume_fault()
            raise TransientError("mock timeout after settle executed")
        return SettleResult(tx_hash=tx, network="mock")


class MockBuyer:
    """contracts.buyer: 'GET resource -> reads 402 offer -> retries with
    payment header'. Drives the REAL Merchant per-request logic directly
    (no HTTP — the daemon process is infrastructure, not contract).

    Each method is one failure mode from the manifest, plus purchase()
    as the well-behaved baseline. Traffic runs in scenario arm(), before
    the operator policy wakes up — the serve loop lives in the daemon,
    not in the subject under test.
    """

    def __init__(self, rng: random.Random, merchant):
        self.rng = rng
        self.merchant = merchant
        self.address = "0x" + _hex(rng, 40)
        self.last_nonce: str | None = None
        self.responses: list[int] = []     # status codes seen

    # -- request plumbing --------------------------------------------------
    def _request(self, header: str | None):
        resp = self.merchant.handle_safe("/resource", header)
        self.responses.append(resp.code)
        return resp

    def _offer(self) -> dict:
        resp = self._request(None)
        assert resp.code == 402, f"expected 402 offer, got {resp.code}"
        return json.loads(resp.body)["accepts"][0]

    def _header(self, offer: dict, value: int | None = None,
                nonce: str | None = None, signature: str = "ok") -> str:
        self.last_nonce = nonce or "0x" + _hex(self.rng, 64)
        payload = {"x402Version": 1, "scheme": "exact",
                   "network": offer["network"],
                   "payload": {"signature": signature, "authorization": {
                       "from": self.address, "to": offer["payTo"],
                       "value": str(value if value is not None
                                    else int(offer["maxAmountRequired"])),
                       "nonce": self.last_nonce}}}
        return base64.b64encode(json.dumps(payload).encode()).decode()

    # -- baseline + failure modes -------------------------------------------
    def purchase(self):
        """Well-behaved buy: offer -> pay exact price -> resource."""
        offer = self._offer()
        return self._request(self._header(offer))

    def purchase_false_payment(self):
        """facilitator/false-payment: complete-looking but forged payment."""
        offer = self._offer()
        return self._request(self._header(offer, signature="forged"))

    def underpay(self):
        """buyer/underpayment: authorization below the offered price."""
        offer = self._offer()
        value = max(1, int(offer["maxAmountRequired"]) // 2)
        return self._request(self._header(offer, value=value))

    def replay(self):
        """buyer/replayed-authorization: resend the last served nonce."""
        assert self.last_nonce, "replay() needs a prior purchase()"
        offer = self._offer()
        return self._request(self._header(offer, nonce=self.last_nonce))

    def malformed(self):
        """buyer/malformed-header: not base64 JSON at all."""
        return self._request("!!not-base64-x402!!")

    def abandon(self):
        """buyer/abandons-after-offer: takes the offer, never pays."""
        return self._offer()

    def flood(self, n: int = 50):
        """buyer/floods-unpaid-requests: n unpaid GETs in a burst."""
        for _ in range(n):
            self._request(None)
