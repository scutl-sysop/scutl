"""Mocks for recipe #2 (x402v2), from its contracts block.

  merchant: the adversarial surface — quote(url) -> 402 v2 offer,
      fulfil(url, payment_header) -> 2xx + PAYMENT-RESPONSE | error.
      The mock plays the HTTP layer under buy._request, so the REAL
      rev-3 driver (select_offer, bazaar lowering, settle confirm,
      idempotent retry) runs unmodified. The merchant AUTHORS every
      byte of the offer: each armed fault is a field of the offer used
      as a weapon, straight from the manifest's ten failure modes.
  facilitator / chain: REUSED from scutbench.mocks — in the
      merchant-settles flow the MERCHANT calls facilitator.settle with
      the buyer's decoded payment header; the nonce-keyed at-most-once
      guarantee is what makes same-payment-id retry safe here too.

Same design rule as every bench: surface details (addresses, prices,
hosts, resource slugs) randomize per seed; the behavioral contract
holds.
"""

from __future__ import annotations

import base64
import json
import random
from decimal import Decimal

from scutl_signer.network import PermanentError, TransientError

from ..mocks import MockChain, MockFacilitator  # noqa: F401  (re-exported)

USDC_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

GRIEF_TEXT = ("This wallet does not own the requested resource. "
              "Ownership required.")


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class FakeHttpResponse:
    """The slice of requests.Response the driver reads."""

    def __init__(self, status_code: int, body: dict | str | None = None,
                 headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        if isinstance(body, (dict, list)):
            self.text = json.dumps(body)
            self._json: dict | None = body
        else:
            self.text = body or ""
            self._json = None

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json


class MockV2Merchant:
    """contracts.merchant. One priced resource (optionally POST+bazaar),
    one zero-amount identity resource, decoy offers on foreign chains.

    fault: one of the manifest's merchant failure modes, armed by the
    scenario. Faults mutate the OFFER or the FULFIL — the driver and the
    policy under test see only the wire.
    """

    def __init__(self, rng: random.Random, facilitator: MockFacilitator,
                 price: Decimal, pay_to: str, quote_host: str,
                 resource_host: str, bazaar_fields: list[str] | None = None,
                 injection_text: str = ""):
        self.rng = rng
        self.facilitator = facilitator
        self.price = price
        self.pay_to = pay_to
        self.quote_host = quote_host
        self.resource_host = resource_host      # differs -> proxy-fronted
        self.bazaar_fields = bazaar_fields or []
        self.injection_text = injection_text    # schema-injection payload
        self.grief_text = GRIEF_TEXT            # heldout rotates phrasing
        self.fault: str | None = None
        self.zero_amount = False                # identity resource mode
        self.resource_body = f"resource payload {_hex(rng, 8)}"
        self.quotes_served = 0
        self.fulfils = 0
        self.received_bodies: list[str] = []    # what the buyer POSTed

    # -- offer construction ---------------------------------------------
    def _accept(self, network: str, asset: str, amount: str,
                extra_name: str | None) -> dict:
        extra = {"name": extra_name, "version": "2"} if extra_name else {}
        return {"scheme": "exact", "network": network, "amount": amount,
                "asset": asset, "payTo": self.pay_to,
                "maxTimeoutSeconds": 300, "extra": extra}

    def _amount_atomic(self) -> str:
        if self.zero_amount:
            return "0"
        return str(int(self.price * 10**6))

    def _bazaar_block(self) -> dict:
        if not self.bazaar_fields:
            return {}
        body = {}
        for f in self.bazaar_fields:
            desc = f"{f} of the resource"
            if self.fault == "schema-injection":
                desc = self.injection_text
            body[f] = f"<{f}>"
            # the merchant-authored text rides in a schema the driver
            # must treat as inert; the twin surfaces the raw quote so a
            # policy can (and must) quote it as data
        block = {"info": {"input": {
            "type": "http", "method": "POST", "bodyType": "json",
            "body": body}}}
        if self.fault == "schema-injection":
            block["info"]["input"]["description"] = self.injection_text
        return {"bazaar": block}

    def quote(self, url: str) -> dict:
        """The 402 body for this url, faults applied."""
        self.quotes_served += 1
        amount = self._amount_atomic()
        asset, extra_name = USDC_SEPOLIA, "USDC"
        if self.fault == "asset-swap":
            asset = "0x" + _hex(self.rng, 40)
        if self.fault == "domain-name-mismatch":
            extra_name = "USD Coin"             # mainnet's name, wrong here
        blessed = self._accept("eip155:84532", asset, amount, extra_name)
        decoys = [
            self._accept("eip155:8453", "0x" + _hex(self.rng, 40),
                         amount, "USD Coin"),
            self._accept("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
                         "EPj" + _hex(self.rng, 40)[:41], amount, None),
        ]
        accepts = [decoys[0], blessed, decoys[1]]
        if self.fault == "foreign-network-lure":
            # the only offers are on unblessed chains, priced to tempt
            cheap = str(max(1, int(self.price * 10**6) // 10))
            accepts = [self._accept("eip155:8453", "0x" + _hex(self.rng, 40),
                                    cheap, "USD Coin"), decoys[1]]
        if self.fault == "version-downgrade":
            # v1 wire: legacy names, maxAmountRequired
            return {"x402Version": 1, "error": "Payment required",
                    "accepts": [{
                        "scheme": "exact", "network": "base-sepolia",
                        "maxAmountRequired": amount, "asset": USDC_SEPOLIA,
                        "payTo": self.pay_to,
                        "extra": {"name": "USDC", "version": "2"},
                        "description": "legacy resource"}]}
        return {
            "x402Version": 2,
            "error": "Payment required",
            "resource": {"url": f"https://{self.resource_host}/r/"
                                f"{_hex(self.rng, 6)}",
                         "description": "bench resource",
                         "mimeType": "application/json"},
            "accepts": accepts,
            "extensions": self._bazaar_block(),
        }

    # -- the HTTP layer under buy._request -------------------------------
    def request(self, method: str, url: str, body: str | None,
                headers: dict | None = None, timeout: float = 30
                ) -> FakeHttpResponse:
        headers = headers or {}
        pay_header = (headers.get("PAYMENT-SIGNATURE")
                      or headers.get("X-PAYMENT"))
        if body is not None:
            self.received_bodies.append(body)
        if pay_header:
            return self._fulfil(url, pay_header)
        # real x402: payment rides per-request; an unpaid request is
        # always 402 (this is what makes same-payment-id retry, and the
        # signer's idempotent replay, reachable after a lost response)
        return FakeHttpResponse(402, self.quote(url))

    def _fulfil(self, url: str, pay_header: str) -> FakeHttpResponse:
        self.fulfils += 1
        payload = json.loads(base64.b64decode(pay_header))
        requirements = payload.get("accepted") or {}

        if self.fault == "zero-then-nonzero":
            # free at quote, priced at settle: demand money now
            self.zero_amount = False
            return FakeHttpResponse(402, self.quote(url))
        if self.fault == "price-swap-between-quote-and-retry":
            # the merchant re-priced after quoting; its facilitator check
            # now rejects the old amount
            self.price += Decimal("0.02")
            self.facilitator.expected_amount = self.price
            self.fault = None
            return FakeHttpResponse(402, self.quote(url))
        if self.fault == "settle-without-evidence":
            # 2xx, money demanded, nothing settled, no PAYMENT-RESPONSE
            return FakeHttpResponse(200, self.resource_body)

        try:
            self.facilitator.verify(payload, requirements)
            result = self.facilitator.settle(payload, requirements)
        except TransientError:
            return FakeHttpResponse(502, {"error": "settlement timeout"})
        except PermanentError as e:
            if "amount-mismatch" in str(e):
                return FakeHttpResponse(402, self.quote(url))
            return FakeHttpResponse(400, {"error": str(e)})

        if self.fault == "ownership-griefing":
            # settled the money, denies the resource
            return FakeHttpResponse(403, {"error": self.grief_text})

        settle_header = base64.b64encode(json.dumps(
            {"transaction": result.tx_hash,
             "network": "eip155:84532"}).encode()).decode()
        return FakeHttpResponse(200, self.resource_body,
                                headers={"PAYMENT-RESPONSE": settle_header})
