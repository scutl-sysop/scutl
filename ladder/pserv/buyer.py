"""Ladder buyer for recipe #2 (paid-service) rungs — the composition edge.

The manifest says "the ladder harness does the buying": a wallet installed
from recipe #1 purchases from the recipe-#2 merchant the model is operating.
This is that buyer. It polls the merchant's port until it serves a 402
offer (the model must have started the daemon), then makes ONE purchase
through the LIVE x402.org facilitator with a unique payment id, and writes a
one-line JSON result. Real Base Sepolia settlement — the same rail wallet
rev 1 ships.

    SCUTL_STATE=<buyer_state> python buyer.py <url> <payment_id> <out_json> [timeout_s]

Exit 0 on a settled 200, nonzero otherwise (the grader reads out_json, not
this exit — but a clean exit keeps the rep log readable).
"""

from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal

import requests

from scutl_signer.core import Signer


def run(url: str, payment_id: str, out_path: str, timeout_s: float) -> int:
    result: dict = {"payment_id": payment_id, "url": url}
    deadline = time.time() + timeout_s

    # Wait for the model to bring the merchant up (402 = offer ready).
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5).status_code == 402:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        result["error"] = "merchant never served a 402 offer before timeout"
        _write(out_path, result)
        return 1

    signer = Signer()
    quote = requests.get(url, timeout=30)
    reqs = quote.json()["accepts"][0]
    amount = Decimal(reqs["maxAmountRequired"]).scaleb(-6)
    auth = signer.authorize(payment_id, reqs["payTo"], amount)
    resp = requests.get(url, headers={"X-PAYMENT": auth["header"]}, timeout=120)
    result["status"] = resp.status_code
    if resp.status_code != 200:
        result["error"] = f"purchase failed: {resp.status_code} {resp.text[:200]}"
        _write(out_path, result)
        return 1

    import base64
    settle = json.loads(base64.b64decode(resp.headers["X-PAYMENT-RESPONSE"]))
    record = signer.record_settled(
        payment_id, reqs["payTo"], amount, settle["transaction"])
    result.update(content=resp.text, pay_to=reqs["payTo"],
                  amount=str(amount), tx=settle["transaction"],
                  chain_status=record["chain_status"],
                  # the exact settled header, so public_probes.py can
                  # re-present it for the public-side replay check
                  x_payment=auth["header"])
    _write(out_path, result)
    return 0


def _write(path: str, obj: dict) -> None:
    with open(path, "w") as f:
        f.write(json.dumps(obj) + "\n")


if __name__ == "__main__":
    url, payment_id, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    timeout_s = float(sys.argv[4]) if len(sys.argv) > 4 else 120.0
    sys.exit(run(url, payment_id, out_path, timeout_s))
