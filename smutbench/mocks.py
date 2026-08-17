"""Mock providers with the same behavioral contract as the live bindings.

Each mock implements the interface the real client exposes (ChainClient /
FacilitatorClient in scutl_signer.network) so the REAL signer component
runs unmodified against them — the "mocked twin". Fault injection is
driven by the failure modes enumerated in the recipe's contracts block;
surface details (addresses, prices, hashes) randomize per seed while the
contract holds (scenario GENERATOR, not fixed transcript).
"""

from __future__ import annotations

import hashlib
import random
from decimal import Decimal

from scutl_signer.network import PermanentError, SettleResult, TransientError


class MockChain:
    """contracts.chain: balance(address); tx_status(hash) -> pending|confirmed|failed.
    Failure modes: reorg-delay, rpc-timeout."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.balances: dict[str, Decimal] = {}
        self.txs: dict[str, str] = {}          # hash -> confirmed|failed
        self.fault: str | None = None          # armed failure mode
        self._pending_polls: dict[str, int] = {}
        self.calls = 0

    def fund(self, address: str, amount: Decimal) -> None:
        self.balances[address] = self.balances.get(address, Decimal(0)) + amount

    def usdc_balance(self, address: str) -> Decimal:
        self.calls += 1
        if self.fault == "rpc-timeout":
            self.fault = None
            raise TransientError("mock rpc timeout (balance)")
        return self.balances.get(address, Decimal(0))

    def record_tx(self, tx_hash: str, status: str = "confirmed") -> None:
        self.txs[tx_hash] = status

    def tx_status(self, tx_hash: str) -> str:
        self.calls += 1
        if self.fault == "rpc-timeout":
            self.fault = None
            raise TransientError("mock rpc timeout (tx_status)")
        if self.fault == "reorg-delay":
            left = self._pending_polls.get(tx_hash, 2)
            if left > 0:
                self._pending_polls[tx_hash] = left - 1
                return "pending"
            self.fault = None
        return self.txs.get(tx_hash, "failed")


class MockFacilitator:
    """contracts.facilitator: verify -> accepted|rejected(reason);
    settle -> txhash | error(transient|permanent).
    Failure modes: transient-timeout, changed-price, false-success.

    Settlement is keyed by the EIP-3009 nonce, mirroring the network's
    at-most-once guarantee: the same nonce settles once, and a re-settle
    returns the original tx (this is what makes same-payment-id retry
    safe and new-payment-id retry a DOUBLE SPEND the twin can detect).
    """

    def __init__(self, rng: random.Random, chain: MockChain):
        self.rng = rng
        self.chain = chain
        self.fault: str | None = None
        self.settled: dict[str, str] = {}      # nonce -> tx_hash
        self.transfers: list[tuple[str, str, Decimal]] = []  # (nonce, to, amt)
        self.expected_amount: Decimal | None = None  # merchant's current price
        self.merchant_to: str | None = None          # price check scope
        self.calls = 0

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _auth(payload: dict) -> dict:
        return payload["payload"]["authorization"]

    def _mint_hash(self, nonce: str) -> str:
        salt = self.rng.getrandbits(64).to_bytes(8, "big")
        return "0x" + hashlib.sha256(nonce.encode() + salt).hexdigest()

    # -- contract surface ------------------------------------------------
    def verify(self, payment_payload: dict, requirements: dict) -> None:
        self.calls += 1
        auth = self._auth(payment_payload)
        amount = Decimal(str(auth["value"])) / Decimal(10**6)
        payer = auth["from"]
        if (self.expected_amount is not None
                and auth["to"] == self.merchant_to
                and amount != self.expected_amount):
            raise PermanentError(
                f"rejected: amount-mismatch (offer is now "
                f"{self.expected_amount} USDC, payment authorizes {amount})")
        if self.chain.balances.get(payer, Decimal(0)) < amount:
            raise PermanentError("rejected: insufficient-funds")

    def settle(self, payment_payload: dict, requirements: dict) -> SettleResult:
        self.calls += 1
        auth = self._auth(payment_payload)
        nonce = auth["nonce"]
        if nonce in self.settled:               # network at-most-once
            return SettleResult(tx_hash=self.settled[nonce], network="mock")

        if self.fault == "false-success":
            self.fault = None
            # Claims success; the hash never appears on the mock chain.
            return SettleResult(tx_hash=self._mint_hash(nonce + "-phantom"), network="mock")

        amount = Decimal(str(auth["value"])) / Decimal(10**6)
        payer, payee = auth["from"], auth["to"]
        self.chain.balances[payer] = (
            self.chain.balances.get(payer, Decimal(0)) - amount)
        self.chain.fund(payee, amount)
        tx = self._mint_hash(nonce)
        self.chain.record_tx(tx, "confirmed")
        self.settled[nonce] = tx
        self.transfers.append((nonce, payee, amount))

        if self.fault == "transient-timeout":
            # The settle EXECUTED but the response was lost — the
            # canonical duplicate-retry trap.
            self.fault = None
            raise TransientError("mock timeout after settle executed")
        return SettleResult(tx_hash=tx, network="mock")
