"""Acceptance driver: buy an x402-priced resource with the signer.

Usage: .venv/bin/python acceptance/purchase.py <url> <payment-id>

Flow (client side of x402): GET -> 402 + requirements -> signer.authorize
(cap-checked, signed header) -> GET with X-PAYMENT -> merchant verifies and
settles via facilitator -> decode X-PAYMENT-RESPONSE -> confirm the settle
tx on-chain -> record the spend. Never trusts the merchant's 200 alone.
"""

from __future__ import annotations

import base64
import json
import sys
from decimal import Decimal

import requests

from scutl_signer.core import Signer
from scutl_signer.network import atomic_to_usdc


def main() -> None:
    url, payment_id = sys.argv[1], sys.argv[2]
    signer = Signer()

    quote = requests.get(url, timeout=30)
    if quote.status_code != 402:
        sys.exit(f"expected 402, got {quote.status_code}")
    reqs = quote.json()["accepts"][0]
    assert reqs["network"] == "base-sepolia", f"wrong network: {reqs['network']}"
    amount = atomic_to_usdc(int(reqs["maxAmountRequired"]))
    print(json.dumps({"quote": {"amount_usdc": str(amount),
                                "pay_to": reqs["payTo"],
                                "description": reqs.get("description", "")}}))

    auth = signer.authorize(payment_id, reqs["payTo"], Decimal(amount))
    if auth.get("idempotent_replay"):
        print(json.dumps({"replay": auth}))
        return

    resp = requests.get(url, headers={"X-PAYMENT": auth["header"]}, timeout=120)
    if resp.status_code != 200:
        sys.exit(f"purchase failed: {resp.status_code} {resp.text[:300]}")

    settle = json.loads(base64.b64decode(resp.headers["X-PAYMENT-RESPONSE"]))
    record = signer.record_settled(
        payment_id, reqs["payTo"], Decimal(amount), settle["transaction"])
    print(json.dumps({"content": resp.text, "spend_record": record}))


if __name__ == "__main__":
    main()
