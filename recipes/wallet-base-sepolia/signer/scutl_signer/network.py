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

USDC_DECIMALS = 6


@dataclass(frozen=True)
class NetworkBinding:
    """bindings.live, one network's worth. Only networks in BLESSED can be
    selected; everything chain-specific the signer needs lives here rather
    than in module constants (rev 2, cst-8ih.14)."""

    caip: str            # CAIP-2 id, the v2 wire name
    legacy_name: str     # v1 wire name
    chain_id: int
    rpc_urls: tuple[str, ...]
    usdc_address: str
    # EIP-712 domain name of the USDC contract. NOT the same everywhere:
    # Sepolia's contract says "USDC", mainnet's says "USD Coin" — a wrong
    # name yields a signature the chain rejects after the merchant may
    # already have surrendered the resource.
    eip712_name: str
    facilitator_url: str | None   # None: merchant-settles flows only
    testnet: bool


BLESSED: dict[str, NetworkBinding] = {
    # Base Sepolia — the free dev/test rail; x402.org facilitator is
    # keyless. sepolia.base.org 502s in windows (rpc-timeout, observed
    # live 2026-08-12) hence the independent-operator fallback.
    "eip155:84532": NetworkBinding(
        caip="eip155:84532",
        legacy_name="base-sepolia",
        chain_id=84532,
        rpc_urls=("https://sepolia.base.org",
                  "https://base-sepolia-rpc.publicnode.com"),
        usdc_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        eip712_name="USDC",
        facilitator_url="https://x402.org/facilitator",
        testnet=True,
    ),
    # Base mainnet — real money. No blessed facilitator: pay() (we settle)
    # is refused here; only merchant-settles purchases (x402-buy) run, so
    # every mainnet spend is bounded by the offer we counter-signed.
    "eip155:8453": NetworkBinding(
        caip="eip155:8453",
        legacy_name="base",
        chain_id=8453,
        rpc_urls=("https://mainnet.base.org",
                  "https://base-rpc.publicnode.com"),
        usdc_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        eip712_name="USD Coin",
        facilitator_url=None,
        testnet=False,
    ),
}

# Accept either wire spelling when selecting/matching a network.
_ALIASES = {b.legacy_name: caip for caip, b in BLESSED.items()}

DEFAULT_NETWORK = "eip155:84532"


def resolve_binding(name: str) -> NetworkBinding:
    caip = _ALIASES.get(name, name)
    if caip not in BLESSED:
        raise PermanentError(
            f"network {name!r} is not blessed; policy is Base only "
            f"({', '.join(BLESSED)})")
    return BLESSED[caip]


_SEPOLIA = BLESSED["eip155:84532"]
# Legacy module-level constants (rev 1 surface; sepolia by definition).
CHAIN_ID = _SEPOLIA.chain_id
RPC_URL = _SEPOLIA.rpc_urls[0]
RPC_FALLBACK = _SEPOLIA.rpc_urls[1]
FACILITATOR_URL = _SEPOLIA.facilitator_url
USDC_ADDRESS = _SEPOLIA.usdc_address


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
                 fallback_url: str | None = RPC_FALLBACK,
                 usdc_address: str = USDC_ADDRESS):
        self.rpc_urls = [rpc_url] + ([fallback_url] if fallback_url else [])
        self.timeout = timeout
        self.usdc_address = usdc_address

    @classmethod
    def for_binding(cls, binding: NetworkBinding,
                    timeout: float = 15.0) -> "ChainClient":
        return cls(rpc_url=binding.rpc_urls[0], timeout=timeout,
                   fallback_url=(binding.rpc_urls[1]
                                 if len(binding.rpc_urls) > 1 else None),
                   usdc_address=binding.usdc_address)

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
        result = self._rpc(
            "eth_call", [{"to": self.usdc_address, "data": data}, "latest"])
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
            {"x402Version": payment_payload.get("x402Version", 1),
             "paymentPayload": payment_payload,
             "paymentRequirements": requirements},
        )
        if not body.get("isValid", False):
            raise PermanentError(f"rejected: {body.get('invalidReason', 'unknown')}")

    def settle(self, payment_payload: dict, requirements: dict) -> SettleResult:
        body = self._post(
            "/settle",
            {"x402Version": payment_payload.get("x402Version", 1),
             "paymentPayload": payment_payload,
             "paymentRequirements": requirements},
        )
        # contracts.facilitator failure mode 'false-success': callers MUST
        # confirm the returned tx on-chain (ChainClient.tx_status) rather
        # than trusting success here.
        if not body.get("success", False):
            raise PermanentError(f"settle failed: {body.get('errorReason', body)}")
        return SettleResult(tx_hash=body["transaction"], network=body.get("network", ""))


def select_offer(quote: dict, binding: NetworkBinding) -> dict:
    """Pick this wallet's offer out of a 402 body, v1 or v2, and refuse
    anything that could produce a signature-vs-spend mismatch.

    v2 quotes routinely carry offers on several chains (AgentMail sends
    five); policy is: only the active binding's network is considered,
    the offer's asset must be the binding's USDC contract, and the
    offer's EIP-712 domain name (extra.name) must match the binding —
    a wrong domain name signs an authorization the chain will reject
    after the merchant may already count us as paid."""
    version = quote.get("x402Version", 1)
    accepts = quote.get("accepts") or []
    names = {binding.caip, binding.legacy_name}
    matching = [r for r in accepts
                if r.get("scheme") == "exact" and r.get("network") in names]
    if not matching:
        offered = sorted({r.get("network", "?") for r in accepts})
        raise PermanentError(
            f"no exact-scheme offer on {binding.caip}; offered: {offered}")
    reqs = matching[0]
    asset = reqs.get("asset", "")
    if asset.lower() != binding.usdc_address.lower():
        raise PermanentError(
            f"offer asset {asset} is not blessed USDC "
            f"{binding.usdc_address} on {binding.caip}; not paying")
    extra = reqs.get("extra") or {}
    if extra.get("name") and extra["name"] != binding.eip712_name:
        raise PermanentError(
            f"offer EIP-712 domain name {extra['name']!r} != binding "
            f"{binding.eip712_name!r}; signature would not verify")
    atomic = reqs["amount"] if version >= 2 else reqs["maxAmountRequired"]
    return {
        "version": version,
        "requirements": reqs,
        "amount_atomic": int(atomic),
        "pay_to": reqs["payTo"],
        "resource": quote.get("resource"),
        "extensions": quote.get("extensions") or {},
    }


def encode_payment_header(payment_payload: dict) -> str:
    """X-PAYMENT header value: base64(JSON payload) per x402 spec."""
    return base64.b64encode(
        json.dumps(payment_payload, separators=(",", ":")).encode()
    ).decode()
