"""Network layer: chain RPC + x402 facilitator client.

This module is the *live binding* (recipe.yaml bindings.live) behind the
contracts in recipe.yaml. SMUTbench mocks replace this module's classes
while honoring the same contracts (ops + failure modes).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal

import requests

# bindings.live — Base Sepolia (chain_id 84532), the only blessed network.
CHAIN_ID = 84532
RPC_URL = "https://sepolia.base.org"
# Same chain (84532), independent operator — sepolia.base.org 502s in
# windows (contract failure mode rpc-timeout, observed live 2026-08-12).
RPC_FALLBACK = "https://base-sepolia-rpc.publicnode.com"
FACILITATOR_URL = "https://x402.org/facilitator"
# Circle USDC on Base Sepolia; 6 decimals.
USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6


def usdc_to_atomic(amount: Decimal) -> int:
    return int(amount.scaleb(USDC_DECIMALS))


def atomic_to_usdc(value: int) -> Decimal:
    return Decimal(value).scaleb(-USDC_DECIMALS)


class TransientError(Exception):
    """Contract failure mode: transient-timeout / rpc-timeout. Retry is safe."""


class PermanentError(Exception):
    """Facilitator rejected or chain reports failure. Do not retry blindly."""


class ChainClient:
    """contracts.chain: balance(address), tx_status(hash)."""

    def __init__(self, rpc_url: str = RPC_URL, timeout: float = 15.0,
                 fallback_url: str | None = RPC_FALLBACK):
        self.rpc_urls = [rpc_url] + ([fallback_url] if fallback_url else [])
        self.timeout = timeout

    def _rpc(self, method: str, params: list) -> dict | str | None:
        last: Exception | None = None
        for url in self.rpc_urls:
            try:
                resp = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1,
                          "method": method, "params": params},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                last = e
        else:
            raise TransientError(f"rpc-timeout: {last}") from last
        body = resp.json()
        if "error" in body:
            raise PermanentError(f"rpc error: {body['error']}")
        return body["result"]

    def usdc_balance(self, address: str) -> Decimal:
        # balanceOf(address) selector 0x70a08231
        data = "0x70a08231" + address.lower().removeprefix("0x").rjust(64, "0")
        result = self._rpc("eth_call", [{"to": USDC_ADDRESS, "data": data}, "latest"])
        return atomic_to_usdc(int(result, 16))

    def tx_status(self, tx_hash: str) -> str:
        receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return "pending"
        return "confirmed" if int(receipt["status"], 16) == 1 else "failed"


@dataclass
class SettleResult:
    tx_hash: str
    network: str


class FacilitatorClient:
    """contracts.facilitator: verify(payment), settle(payment)."""

    def __init__(self, base_url: str = FACILITATOR_URL, timeout: float = 30.0):
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
        # contracts.facilitator failure mode 'false-success': callers MUST
        # confirm the returned tx on-chain (ChainClient.tx_status) rather
        # than trusting success here.
        if not body.get("success", False):
            raise PermanentError(f"settle failed: {body.get('errorReason', body)}")
        return SettleResult(tx_hash=body["transaction"], network=body.get("network", ""))


def encode_payment_header(payment_payload: dict) -> str:
    """X-PAYMENT header value: base64(JSON payload) per x402 spec."""
    return base64.b64encode(
        json.dumps(payment_payload, separators=(",", ":")).encode()
    ).decode()
