"""Acceptance fixture: a minimal x402-priced resource server.

Serves one resource at /haiku for 0.01 USDC on Base Sepolia. Payment
verification and settlement go through the LIVE x402.org facilitator, so a
successful purchase exercises the real payment rail end-to-end; only the
merchant is local. The recipient wallet is ephemeral (generated at start,
address printed) — this is a testnet fixture, not a treasury.

rev 2 (cst-8ih.14): --v2 serves the x402 v2 wire shape — CAIP network id,
`amount` field, PAYMENT-SIGNATURE request header, PAYMENT-RESPONSE reply
header, POST /inbox with a JSON body (mimics AgentMail's create-inbox
shape), and a zero-amount GET /whoami (wallet-as-identity call, no
settlement). If the live facilitator rejects the v2 envelope, the
merchant transparently retries it translated to v1 — the client's v2
signature is over the same EIP-3009 authorization either way.

Run:  .venv/bin/python acceptance/resource_server.py [port] [--v2]
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
NETWORK_V1 = "base-sepolia"
NETWORK_V2 = "eip155:84532"
CHAIN_ID = 84532

RECIPIENT = Account.create()
V2_MODE = False

HAIKU = """the toll gate opens —
six cents of moonlight later
the road forgets you
"""


def requirements_v1(resource_url: str) -> dict:
    return {
        "scheme": "exact",
        "network": NETWORK_V1,
        "maxAmountRequired": PRICE_ATOMIC,
        "resource": resource_url,
        "description": "one haiku, x402-priced (scutl acceptance fixture)",
        "mimeType": "text/plain",
        "payTo": RECIPIENT.address,
        "maxTimeoutSeconds": 120,
        "asset": USDC,
        "extra": {"name": "USDC", "version": "2"},
    }


def requirements_v2(amount_atomic: str) -> dict:
    return {
        "scheme": "exact",
        "network": NETWORK_V2,
        "amount": amount_atomic,
        "asset": USDC,
        "payTo": RECIPIENT.address,
        "maxTimeoutSeconds": 120,
        "extra": {"name": "USDC", "version": "2"},
    }


def payment_required_v2(resource_url: str, amount_atomic: str,
                        description: str, mime: str) -> dict:
    return {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": resource_url, "description": description,
                     "mimeType": mime},
        "accepts": [requirements_v2(amount_atomic)],
        "extensions": {},
    }


def _to_v1(payload: dict, reqs_v2: dict, resource_url: str) -> tuple[dict, dict]:
    """Translate a v2 PaymentPayload + PaymentRequirements to the v1
    envelope the x402.org facilitator historically accepts. The signature
    is unchanged — it's over the same EIP-3009 authorization; only the
    JSON packaging (and int-vs-string values) differs."""
    auth = payload["payload"]["authorization"]
    auth_v1 = {**auth, "value": int(auth["value"]),
               "validAfter": int(auth["validAfter"]),
               "validBefore": int(auth["validBefore"])}
    domain = {"name": "USDC", "version": "2", "chainId": CHAIN_ID,
              "verifyingContract": USDC}
    types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ],
    }
    payload_v1 = {
        "x402Version": 1, "scheme": "exact", "network": NETWORK_V1,
        "domain": domain, "types": types,
        "payload": {"signature": payload["payload"]["signature"],
                    "authorization": auth_v1},
    }
    reqs_v1 = {
        "scheme": "exact", "network": NETWORK_V1,
        "maxAmountRequired": reqs_v2["amount"],
        "resource": resource_url, "description": "v2->v1 translation",
        "mimeType": "application/json",
        "payTo": reqs_v2["payTo"], "maxTimeoutSeconds": 120,
        "asset": USDC, "extra": reqs_v2.get("extra", {}),
    }
    return payload_v1, reqs_v1


def facilitate(endpoint: str, envelopes: list[tuple[dict, dict, int]]
               ) -> tuple[dict | None, bool]:
    """POST /verify or /settle, retrying transient failures with backoff.
    envelopes: (paymentPayload, paymentRequirements, x402Version) tried in
    order — a 4xx verdict on one envelope falls through to the next
    (v2-native first, v1 translation second), and a verdict on the LAST
    envelope is final."""
    body, ok = None, False
    for i, (payload, reqs, version) in enumerate(envelopes):
        last = i == len(envelopes) - 1
        for delay in (0, 10, 30, 60):
            if delay:
                time.sleep(delay)
            try:
                resp = requests.post(
                    FACILITATOR + endpoint,
                    json={"x402Version": version, "paymentPayload": payload,
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
        if ok or not last:
            if ok:
                return body, True
            continue
    return body, ok


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict, extra_headers: dict | None = None
              ) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- v1 surface (unchanged from rev 1) ------------------------------
    def _serve_v1_haiku(self):
        reqs = requirements_v1(
            f"http://{self.headers.get('Host', 'localhost')}/haiku")
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
            body, ok = facilitate(endpoint, [(payload, reqs, 1)])
            if not ok:
                self._json(402, {"x402Version": 1, "accepts": [reqs],
                                 "error": f"{endpoint} failed: {body}"})
                return
        data = HAIKU.encode()
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("X-PAYMENT-RESPONSE",
                         base64.b64encode(json.dumps(body).encode()).decode())
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- v2 surface -----------------------------------------------------
    def _serve_v2(self, amount_atomic: str, description: str,
                  result: dict) -> None:
        url = f"http://{self.headers.get('Host', 'localhost')}{self.path}"
        header = self.headers.get("PAYMENT-SIGNATURE")
        if not header:
            self._json(402, payment_required_v2(
                url, amount_atomic, description, "application/json"))
            return
        try:
            payload = json.loads(base64.b64decode(header))
            accepted = payload["accepted"]
        except Exception:
            self._json(400, {"error": "malformed PAYMENT-SIGNATURE header"})
            return
        if accepted.get("amount") != amount_atomic:
            self._json(402, payment_required_v2(
                url, amount_atomic, "amount mismatch", "application/json"))
            return

        if amount_atomic == "0":
            # Identity-only call: signature received, nothing to settle.
            settle = {"success": True, "transaction": "",
                      "network": NETWORK_V2,
                      "payer": payload["payload"]["authorization"]["from"]}
        else:
            payload_v1, reqs_v1 = _to_v1(payload, accepted, url)
            envelopes = [(payload, accepted, 2), (payload_v1, reqs_v1, 1)]
            for endpoint in ("/verify", "/settle"):
                body, ok = facilitate(endpoint, envelopes)
                if not ok:
                    self._json(402, payment_required_v2(
                        url, amount_atomic,
                        f"{endpoint} failed: {body}", "application/json"))
                    return
            settle = {"success": True,
                      "transaction": body.get("transaction", ""),
                      "network": NETWORK_V2, "payer":
                          payload["payload"]["authorization"]["from"]}
        self._json(200, result, extra_headers={
            "PAYMENT-RESPONSE":
                base64.b64encode(json.dumps(settle).encode()).decode()})

    def do_GET(self):  # noqa: N802
        if not V2_MODE:
            if self.path != "/haiku":
                self._json(404, {"error": "not found"})
                return
            self._serve_v1_haiku()
            return
        if self.path == "/whoami":
            self._serve_v2("0", "wallet-as-identity probe (free, signed)",
                           {"you": "whoever signed that header"})
        elif self.path == "/haiku":
            self._serve_v2(PRICE_ATOMIC,
                           "one haiku, x402 v2-priced (scutl fixture)",
                           {"haiku": HAIKU})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not V2_MODE or self.path != "/inbox":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._json(400, {"error": "body is not JSON"})
            return
        self._serve_v2(PRICE_ATOMIC,
                       "create one inbox (AgentMail-shaped fixture)",
                       {"inbox": f"{body.get('username', 'anon')}@fixture.test",
                        "echo": body})

    def log_message(self, fmt, *args):  # quiet
        print(f"[resource-server] {fmt % args}", file=sys.stderr)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    V2_MODE = "--v2" in args
    ports = [a for a in args if a != "--v2"]
    port = int(ports[0]) if ports else 4021
    print(json.dumps({
        "listening": port, "v2": V2_MODE,
        "resource": f"http://127.0.0.1:{port}/haiku",
        "price_usdc": "0.01", "pay_to": RECIPIENT.address}))
    sys.stdout.flush()
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
