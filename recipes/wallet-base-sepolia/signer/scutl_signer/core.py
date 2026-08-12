"""Signer operations. Every manifest tool (wallet_status / wallet_sign /
wallet_pay / wallet_admin) maps to one method here.

Cap enforcement lives in pay() and nowhere else can lift it. Key material
is loaded, used, and dropped inside a method; it is never part of any
return value or log record.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from . import approvals
from .network import (
    CHAIN_ID,
    USDC_ADDRESS,
    ChainClient,
    FacilitatorClient,
    usdc_to_atomic,
)
from .state import StateDir


class CapExceeded(Exception):
    pass


class Signer:
    def __init__(
        self,
        state: StateDir | None = None,
        chain: ChainClient | None = None,
        facilitator: FacilitatorClient | None = None,
    ):
        self.state = state or StateDir()
        self.chain = chain or ChainClient()
        self.facilitator = facilitator or FacilitatorClient()

    # -- key handling (private throughout) ------------------------------
    def _load_account(self) -> Account:
        kek = self.state.kek.read_text().strip()
        keystore = json.loads(self.state.keystore.read_text())
        return Account.from_key(Account.decrypt(keystore, kek))

    def address(self) -> str:
        return json.loads(self.state.keystore.read_text())["address_checksummed"]

    # -- wallet_admin ----------------------------------------------------
    def keygen(self, cap_per_tx: Decimal, cap_daily: Decimal) -> dict:
        self.state.init()
        self.state.check_not_revoked()
        approvals.consume(self.state, "keygen")
        if self.state.keystore.exists():
            raise RuntimeError("keystore already exists; refusing to overwrite")
        kek = secrets.token_hex(32)
        acct = Account.create()
        keystore = Account.encrypt(acct.key, kek)
        keystore["address_checksummed"] = acct.address
        self.state.write_secret(self.state.kek, kek.encode())
        self.state.write_secret(
            self.state.keystore, json.dumps(keystore).encode()
        )
        self.state.save_caps(cap_per_tx, cap_daily)
        return {"address": acct.address, "caps": {"per_tx": str(cap_per_tx), "daily": str(cap_daily)}}

    def backup_verify(self) -> dict:
        """Agent-side half of the human backup step: record what a valid
        backup must contain. The human copies keystore.json and kek to
        offline storage (stored separately); this just fingerprints them."""
        self.state.check_not_revoked()
        digest = hashlib.sha256(self.state.keystore.read_bytes()).hexdigest()
        self.state.backup_marker.write_text(
            json.dumps({"keystore_sha256": digest,
                        "verified_at": datetime.now(timezone.utc).isoformat()})
        )
        return {"keystore_sha256": digest, "backup_marker": "written"}

    def revoke(self) -> dict:
        """Kill + tombstone. No sweep — zero decisions beyond 'shut it down'
        (cst-8ih.1 ruling 6); funds stay recoverable via the human's backup."""
        approvals.consume(self.state, "revoke")
        address = self.address() if self.state.keystore.exists() else None
        tombstone = {
            "address": address,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }
        # Tombstone first: from this moment every op refuses, even if
        # shredding is interrupted.
        self.state.tombstone.write_text(json.dumps(tombstone))
        for secret_file in (self.state.keystore, self.state.kek):
            if secret_file.exists():
                size = secret_file.stat().st_size
                self.state.write_secret(secret_file, b"\x00" * size)
                secret_file.unlink()
        return tombstone

    # -- wallet_status ----------------------------------------------------
    def status(self) -> dict:
        self.state.check_not_revoked()
        caps = self.state.load_caps()
        return {
            "address": self.address(),
            "network": "base-sepolia",
            "chain_id": CHAIN_ID,
            "usdc_balance": str(self.chain.usdc_balance(self.address())),
            "caps": {k: str(v) for k, v in caps.items()},
            "spent_last_24h": str(self.state.spent_last_24h()),
            "backup_verified": self.state.backup_marker.exists(),
        }

    # -- wallet_sign --------------------------------------------------------
    def sign_message(self, message: str) -> dict:
        self.state.check_not_revoked()
        acct = self._load_account()
        sig = acct.sign_message(encode_defunct(text=message))
        return {"address": acct.address, "signature": sig.signature.hex()}

    # -- wallet_pay -----------------------------------------------------------
    def pay(self, payment_id: str, pay_to: str, amount: Decimal,
            valid_secs: int = 600) -> dict:
        """x402 exact-scheme payment: EIP-3009 transferWithAuthorization.

        Idempotent by payment_id: the authorization nonce derives from it,
        so a duplicate retry re-signs the *same* authorization (the network
        can settle it at most once), and an already-settled payment_id
        returns the original record without paying again.
        """
        self.state.check_not_revoked()

        prior = self.state.settled_by_payment_id(payment_id)
        if prior is not None:
            return {**prior, "idempotent_replay": True}

        # --- cap enforcement: the only gate, and it is in code -----------
        caps = self.state.load_caps()
        if amount > caps["cap_per_tx"]:
            raise CapExceeded(
                f"amount {amount} exceeds per-tx cap {caps['cap_per_tx']}")
        spent = self.state.spent_last_24h()
        if spent + amount > caps["cap_daily"]:
            raise CapExceeded(
                f"amount {amount} + spent {spent} exceeds daily cap {caps['cap_daily']}")

        acct = self._load_account()
        now = int(datetime.now(timezone.utc).timestamp())
        nonce = "0x" + hashlib.sha256(payment_id.encode()).hexdigest()
        authorization = {
            "from": acct.address,
            "to": pay_to,
            "value": str(usdc_to_atomic(amount)),
            "validAfter": str(now - 60),
            "validBefore": str(now + valid_secs),
            "nonce": nonce,
        }
        typed = {
            "types": {
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
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {"name": "USDC", "version": "2",
                       "chainId": CHAIN_ID, "verifyingContract": USDC_ADDRESS},
            "message": {**authorization,
                        "value": int(authorization["value"]),
                        "validAfter": int(authorization["validAfter"]),
                        "validBefore": int(authorization["validBefore"])},
        }
        signature = acct.sign_message(encode_typed_data(full_message=typed))

        payment_payload = {
            "x402Version": 1,
            "scheme": "exact",
            "network": "base-sepolia",
            "payload": {"signature": signature.signature.hex(),
                        "authorization": authorization},
        }
        requirements = {
            "scheme": "exact",
            "network": "base-sepolia",
            "maxAmountRequired": authorization["value"],
            "payTo": pay_to,
            "asset": USDC_ADDRESS,
        }

        self.facilitator.verify(payment_payload, requirements)
        settle = self.facilitator.settle(payment_payload, requirements)
        # Never trust 'success' alone (failure mode: false-success).
        confirmed = self.chain.tx_status(settle.tx_hash)

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "payment_id": payment_id,
            "to": pay_to,
            "amount": str(amount),
            "tx": settle.tx_hash,
            "chain_status": confirmed,
            "status": "settled",
        }
        self.state.append_spend(record)
        return record
