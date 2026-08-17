"""Signer operations. Every manifest tool (wallet_status / wallet_sign /
wallet_pay / wallet_admin) maps to one method here.

Cap enforcement lives in _reserve(), shared by pay() and authorize(),
and nowhere else can lift it: the daily cap counts settled spend plus
outstanding signed authorizations, reserved under a lock before signing. Key material
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
    DEFAULT_NETWORK,
    ChainClient,
    FacilitatorClient,
    NetworkBinding,
    PermanentError,
    resolve_binding,
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
        binding: NetworkBinding | None = None,
    ):
        self.state = state or StateDir()
        self.binding = binding or resolve_binding(
            self.state.load_network() or DEFAULT_NETWORK)
        self.chain = chain or ChainClient.for_binding(self.binding)
        if facilitator is not None:
            self.facilitator = facilitator
        elif self.binding.facilitator_url:
            self.facilitator = FacilitatorClient(self.binding.facilitator_url)
        else:
            self.facilitator = None

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
        # Pin the wallet to its network for life: one key, one chain.
        self.state.save_network(self.binding.caip)
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
            "network": self.binding.caip,
            "network_legacy": self.binding.legacy_name,
            "testnet": self.binding.testnet,
            "chain_id": self.binding.chain_id,
            "usdc_balance": str(self.chain.usdc_balance(self.address())),
            "caps": {k: str(v) for k, v in caps.items()},
            "spent_last_24h": str(self.state.spent_last_24h()),
            "reserved_outstanding": str(
                self.state.cap_exposure() - self.state.spent_last_24h()),
            "backup_verified": self.state.backup_marker.exists(),
        }

    # -- wallet_sign --------------------------------------------------------
    def sign_message(self, message: str) -> dict:
        self.state.check_not_revoked()
        acct = self._load_account()
        sig = acct.sign_message(encode_defunct(text=message))
        return {"address": acct.address, "signature": "0x" + sig.signature.hex()}

    # -- wallet_pay -----------------------------------------------------------
    def _reserve(self, payment_id: str, pay_to: str, amount: Decimal,
                 valid_secs: int) -> dict | None:
        """Idempotency + cap gate shared by pay() and authorize(), with a
        spend reservation (cst-8ih.6). Returns the prior settled record for
        a replayed payment_id.

        Under the cap lock: the daily cap is measured against settled spend
        PLUS outstanding signed authorizations, and an 'authorized' record
        is appended before the signature exists — so N calls before any
        record_settled() each see the previous calls' reservations, and
        cannot jointly sign past cap_daily. The reservation expires with
        the authorization's validBefore (plus clock slack) and is
        superseded by its payment_id's settled record."""
        self.state.check_not_revoked()
        # --- cap enforcement: the only gate, and it is in code -----------
        with self.state.cap_lock():
            prior = self.state.settled_by_payment_id(payment_id)
            if prior is not None:
                return prior
            caps = self.state.load_caps()
            if amount > caps["cap_per_tx"]:
                raise CapExceeded(
                    f"amount {amount} exceeds per-tx cap {caps['cap_per_tx']}")
            now = datetime.now(timezone.utc)
            exposure = self.state.cap_exposure(
                now, exclude_payment_id=payment_id)
            if exposure + amount > caps["cap_daily"]:
                raise CapExceeded(
                    f"amount {amount} + spent/reserved {exposure} exceeds "
                    f"daily cap {caps['cap_daily']}")
            self.state.append_spend({
                "ts": now.isoformat(),
                "payment_id": payment_id,
                "to": pay_to,
                "amount": str(amount),
                "status": "authorized",
                # _build_payment stamps its own validBefore moments later;
                # the slack keeps the reservation alive at least as long.
                "valid_before": int(now.timestamp()) + valid_secs + 60,
            })
        return None

    def _build_payment(self, payment_id: str, pay_to: str, amount: Decimal,
                       valid_secs: int, offer: dict | None = None
                       ) -> tuple[dict, dict]:
        """Sign the EIP-3009 authorization; returns (payload, requirements).

        Without an offer this emits the rev-1 v1 wire shape (pay() and the
        existing acceptance surface). With an offer — the dict from
        network.select_offer() — the wire version follows the offer, and
        for v2 the chosen requirements object is echoed verbatim as
        `accepted` per spec. The EIP-712 domain always comes from the
        binding; select_offer has already refused any offer that
        disagrees with it."""
        acct = self._load_account()
        now = int(datetime.now(timezone.utc).timestamp())
        nonce = "0x" + hashlib.sha256(payment_id.encode()).hexdigest()
        value = usdc_to_atomic(amount)
        valid_after = now - 60
        valid_before = now + valid_secs
        authorization = {
            "from": acct.address,
            "to": pay_to,
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        }
        domain_version = "2"
        if offer is not None:
            domain_version = (offer["requirements"].get("extra") or {}).get(
                "version", "2")
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
            "domain": {"name": self.binding.eip712_name,
                       "version": domain_version,
                       "chainId": self.binding.chain_id,
                       "verifyingContract": self.binding.usdc_address},
            "message": authorization,
        }
        signature = acct.sign_message(encode_typed_data(full_message=typed))

        if offer is not None and offer["version"] >= 2:
            # v2: authorization values are strings on the wire; accepted
            # echoes the offer; resource/extensions echo what was quoted.
            payment_payload = {
                "x402Version": 2,
                "accepted": offer["requirements"],
                "payload": {
                    "signature": "0x" + signature.signature.hex(),
                    "authorization": {
                        **{k: str(v) for k, v in authorization.items()
                           if k in ("value", "validAfter", "validBefore")},
                        "from": acct.address,
                        "to": pay_to,
                        "nonce": nonce,
                    },
                },
                "extensions": offer["extensions"],
            }
            if offer.get("resource"):
                payment_payload["resource"] = offer["resource"]
            return payment_payload, offer["requirements"]

        payment_payload = {
            "x402Version": 1,
            "scheme": "exact",
            "network": self.binding.legacy_name,
            "domain": typed["domain"],
            "types": typed["types"],
            "payload": {"signature": "0x" + signature.signature.hex(),
                        "authorization": authorization},
        }
        requirements = (offer["requirements"] if offer is not None else {
            "scheme": "exact",
            "network": self.binding.legacy_name,
            "maxAmountRequired": str(value),
            "payTo": pay_to,
            "asset": self.binding.usdc_address,
            "extra": {"name": self.binding.eip712_name,
                      "version": domain_version},
        })
        return payment_payload, requirements

    def pay(self, payment_id: str, pay_to: str, amount: Decimal,
            valid_secs: int = 600) -> dict:
        """Direct x402 payment: sign, then verify + settle via the
        facilitator ourselves (agent-pays-endpoint flow).

        Idempotent by payment_id: the authorization nonce derives from it,
        so a duplicate retry re-signs the *same* authorization (the network
        can settle it at most once), and an already-settled payment_id
        returns the original record without paying again.
        """
        if self.facilitator is None:
            raise PermanentError(
                f"pay() needs a blessed facilitator and {self.binding.caip} "
                f"has none; only merchant-settles purchases (x402-buy) run "
                f"on this network")
        prior = self._reserve(payment_id, pay_to, amount, valid_secs)
        if prior is not None:
            return {**prior, "idempotent_replay": True}
        payment_payload, requirements = self._build_payment(
            payment_id, pay_to, amount, valid_secs)

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

    def authorize(self, payment_id: str, pay_to: str, amount: Decimal,
                  valid_secs: int = 600, offer: dict | None = None) -> dict:
        """Merchant-settles x402 purchase flow: cap-check and sign, return
        the payment header for the caller to present; the resource server
        settles. Caller MUST confirm on-chain and then record_settled() —
        an authorized-but-unrecorded payment is spendable by the merchant,
        so record on any 200, and rely on nonce-idempotency for retries.

        offer (from network.select_offer) selects the wire version and, for
        v2, the echoed `accepted` requirements. Zero-amount offers — v2's
        wallet-as-identity calls — flow through the same reservation path;
        a 0 reservation costs no cap headroom but keeps the payment_id
        idempotency and the audit record."""
        from .network import encode_payment_header

        prior = self._reserve(payment_id, pay_to, amount, valid_secs)
        if prior is not None:
            return {**prior, "idempotent_replay": True}
        payment_payload, _ = self._build_payment(
            payment_id, pay_to, amount, valid_secs, offer=offer)
        return {
            "payment_id": payment_id,
            "header": encode_payment_header(payment_payload),
            "x402_version": payment_payload["x402Version"],
            "to": pay_to,
            "amount": str(amount),
        }

    def record_settled(self, payment_id: str, pay_to: str, amount: Decimal,
                       tx_hash: str | None) -> dict:
        # Zero-amount identity calls may settle nothing on-chain; an empty
        # transaction is legitimate there and there is nothing to confirm.
        chain_status = self.chain.tx_status(tx_hash) if tx_hash else "no-tx"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "payment_id": payment_id,
            "to": pay_to,
            "amount": str(amount),
            "tx": tx_hash or "",
            "chain_status": chain_status,
            "status": "settled",
        }
        self.state.append_spend(record)
        return record
