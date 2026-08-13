"""Network layer: the x402 facilitator client, payee side.

This module is the *live binding* (recipe.yaml bindings.live) behind the
contracts in recipe.yaml. SMUTbench mocks replace this module's classes
while honoring the same contracts (ops + failure modes).

Deliberately duplicated from the wallet signer's network module rather than
imported: components are standalone (a merchant install must not drag in a
wallet), and the two see the same contracts from opposite sides.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from decimal import Decimal

import requests

# bindings.live — Base Sepolia (chain_id 84532), the only blessed network.
NETWORK = "base-sepolia"
FACILITATOR_URL = "https://x402.org/facilitator"
# Circle USDC on Base Sepolia; 6 decimals.
USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6


def usdc_to_atomic(amount: Decimal) -> int:
    return int(amount.scaleb(USDC_DECIMALS))


def atomic_to_usdc(value: int) -> Decimal:
    return Decimal(value).scaleb(-USDC_DECIMALS)


class TransientError(Exception):
    """Contract failure mode: transient-timeout. Retry is safe."""


class PermanentError(Exception):
    """Facilitator rejected. Do not retry blindly."""


@dataclass
class SettleResult:
    tx_hash: str
    network: str


class FacilitatorClient:
    """contracts.facilitator: verify(payment, requirements), settle(...)."""

    def __init__(self, base_url: str = FACILITATOR_URL, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}{path}", json=payload, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise TransientError(f"transient-timeout: {e}") from e
        if resp.status_code >= 500:
            raise TransientError(f"facilitator 5xx: {resp.status_code}")
        body = resp.json()
        if resp.status_code >= 400:
            raise PermanentError(f"facilitator rejected: {body}")
        return body

    def verify(self, payment_payload: dict, requirements: dict) -> None:
        body = self._post(
            "/verify",
            {"x402Version": 1, "paymentPayload": payment_payload,
             "paymentRequirements": requirements},
        )
        if not body.get("isValid", False):
            raise PermanentError(f"rejected: {body.get('invalidReason', 'unknown')}")

    def settle(self, payment_payload: dict, requirements: dict) -> SettleResult:
        body = self._post(
            "/settle",
            {"x402Version": 1, "paymentPayload": payment_payload,
             "paymentRequirements": requirements},
        )
        # contracts.facilitator failure mode 'settle-timeout-after-verify':
        # the caller retries TransientError; a success:false body is final.
        if not body.get("success", False):
            raise PermanentError(f"settle failed: {body.get('errorReason', body)}")
        return SettleResult(tx_hash=body["transaction"], network=body.get("network", ""))


def decode_payment_header(header: str) -> dict:
    """X-PAYMENT header value: base64(JSON payload) per x402 spec.

    contracts.buyer failure mode 'malformed-header' surfaces here as
    ValueError; the server answers 400, never 500.
    """
    try:
        return json.loads(base64.b64decode(header, validate=True))
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"malformed X-PAYMENT header: {e}") from e


def encode_payment_response(settle_body: dict) -> str:
    return base64.b64encode(
        json.dumps(settle_body, separators=(",", ":")).encode()
    ).decode()
