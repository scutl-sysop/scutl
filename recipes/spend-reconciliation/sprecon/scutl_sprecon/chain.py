"""Chain evidence leg: discovery, not assertion.

tx_status alone can only verify hashes the books already claim; the
recipe's one new primitive is transfer DISCOVERY — eth_getLogs over the
USDC Transfer topic filtered on the wallet address in both directions,
chunked by block range and resumed from a checkpointed block — so an
unlogged transfer out (the drain) surfaces as a first-class finding
instead of a balance mystery. The client is read-only by construction:
it can call eth_call/eth_getLogs/eth_getTransactionReceipt and nothing
that carries a signature.
"""

from __future__ import annotations

from decimal import Decimal

import requests

TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
GETLOGS_CHUNK = 10_000
USDC_DECIMALS = Decimal(10) ** 6


def _addr_topic(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def _topic_addr(topic: str) -> str:
    return "0x" + topic.removeprefix("0x")[-40:]


class TransientError(Exception):
    """rpc-flap: retry and say so — never guess a balance."""


class ChainAuditClient:
    """transfers(address, from_block), tx_status(hash), usdc_balance(addr),
    head_block(). The test twin implements the same four."""

    def __init__(self, rpc_url: str, usdc_address: str,
                 timeout: float = 15.0):
        self.rpc_url = rpc_url
        self.usdc_address = usdc_address
        self.timeout = timeout

    def _rpc(self, method: str, params: list):
        try:
            resp = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1,
                      "method": method, "params": params},
                timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise TransientError(f"rpc-flap: {e}") from e
        body = resp.json()
        if "error" in body:
            raise TransientError(f"rpc error: {body['error']}")
        return body["result"]

    def head_block(self) -> int:
        return int(self._rpc("eth_blockNumber", []), 16)

    def usdc_balance(self, address: str) -> Decimal:
        data = "0x70a08231" + address.lower().removeprefix("0x").rjust(64, "0")
        result = self._rpc(
            "eth_call", [{"to": self.usdc_address, "data": data}, "latest"])
        return Decimal(int(result, 16)) / USDC_DECIMALS

    def tx_status(self, tx_hash: str) -> str:
        receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return "pending"
        return "confirmed" if int(receipt["status"], 16) == 1 else "failed"

    def transfers(self, address: str, from_block: int,
                  to_block: int | None = None) -> list[dict]:
        """USDC Transfer events touching address, both directions:
        [{direction: in|out, counterparty, amount (Decimal USDC), tx,
          block}], ascending by block."""
        head = to_block if to_block is not None else self.head_block()
        out: list[dict] = []
        for direction, topics in (
                ("out", [TRANSFER_TOPIC, _addr_topic(address)]),
                ("in", [TRANSFER_TOPIC, None, _addr_topic(address)])):
            start = from_block
            while start <= head:
                end = min(start + GETLOGS_CHUNK - 1, head)
                logs = self._rpc("eth_getLogs", [{
                    "address": self.usdc_address,
                    "topics": topics,
                    "fromBlock": hex(start), "toBlock": hex(end)}])
                for lg in logs:
                    frm = _topic_addr(lg["topics"][1])
                    to = _topic_addr(lg["topics"][2])
                    out.append({
                        "direction": direction,
                        "counterparty": to if direction == "out" else frm,
                        "amount": Decimal(int(lg["data"], 16)) / USDC_DECIMALS,
                        "tx": lg["transactionHash"],
                        "block": int(lg["blockNumber"], 16)})
                start = end + 1
        # self-transfers appear in both sweeps; keep one record per (tx, dir)
        seen: set[tuple] = set()
        uniq = []
        for t in sorted(out, key=lambda t: t["block"]):
            key = (t["tx"], t["direction"], t["counterparty"], str(t["amount"]))
            if key not in seen:
                seen.add(key)
                uniq.append(t)
        return uniq
