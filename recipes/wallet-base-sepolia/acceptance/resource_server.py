"""Acceptance fixture: a minimal x402-priced resource server.

Serves one resource at /haiku for 0.01 USDC on Base Sepolia. Payment
verification and settlement go through the LIVE x402.org facilitator, so a
successful purchase exercises the real payment rail end-to-end; only the
merchant is local. The recipient wallet is ephemeral (generated at start,
address printed) — this is a testnet fixture, not a treasury.

Run:  .venv/bin/python acceptance/resource_server.py [port]
"""

from __future__ import annotations

import base64
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from eth_account import Account

FACILITATOR = "https://x402.org/facilitator"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PRICE_ATOMIC = "10000"  # 0.01 USDC, 6 decimals
NETWORK = "base-sepolia"

RECIPIENT = Account.create()

HAIKU = """the toll gate opens —
six cents of moonlight later
the road forgets you
"""


def requirements(resource_url: str) -> dict:
    return {
        "scheme": "exact",
        "network": NETWORK,
        "maxAmountRequired": PRICE_ATOMIC,
        "resource": resource_url,
        "description": "one haiku, x402-priced (scutl acceptance fixture)",
        "mimeType": "text/plain",
        "payTo": RECIPIENT.address,
        "maxTimeoutSeconds": 120,
        "asset": USDC,
        "extra": {"name": "USDC", "version": "2"},
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path != "/haiku":
            self._json(404, {"error": "not found"})
            return
        reqs = requirements(f"http://{self.headers.get('Host', 'localhost')}/haiku")
        header = self.headers.get("X-PAYMENT")
        if not header:
            self._json(402, {"x402Version": 1, "accepts": [reqs],
                             "error": "payment required"})
            return
        try:
            payload = json.loads(base64.b64decode(header))
        except Exception:
            self._json(400, {"error": "malformed X-PAYMENT header"})
            return

        for endpoint in ("/verify", "/settle"):
            # The live facilitator flakes in multi-minute windows
            # (contract failure mode: transient-timeout). A merchant
            # retries 5xx/timeouts with backoff; a 200 with a rejection
            # body is a real verdict and is NOT retried.
            body, ok = None, False
            for delay in (0, 10, 30, 60):
                if delay:
                    time.sleep(delay)
                try:
                    resp = requests.post(
                        FACILITATOR + endpoint,
                        json={"x402Version": 1, "paymentPayload": payload,
                              "paymentRequirements": reqs},
                        timeout=60,
                    )
                except requests.RequestException:
                    continue
                if resp.status_code >= 500:
                    continue
                body = resp.json()
                ok = (body.get("isValid") if endpoint == "/verify"
                      else body.get("success"))
                break
            if not ok:
                self._json(402, {"x402Version": 1, "accepts": [reqs],
                                 "error": f"{endpoint} failed: {body}"})
                return

        settle_body = body
        data = HAIKU.encode()
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header(
            "X-PAYMENT-RESPONSE",
            base64.b64encode(json.dumps(settle_body).encode()).decode(),
        )
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quiet
        print(f"[resource-server] {fmt % args}", file=sys.stderr)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4021
    print(json.dumps({"listening": port, "resource": f"http://127.0.0.1:{port}/haiku",
                      "price_usdc": "0.01", "pay_to": RECIPIENT.address}))
    sys.stdout.flush()
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
