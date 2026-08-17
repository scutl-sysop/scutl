"""rev 2 (cst-8ih.14): x402 v2 offers, network bindings, zero-amount
identity calls. Same mock discipline as test_signer.py; the live v2 rail
runs in the acceptance suite."""

import base64
import json
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from scutl_signer import approvals
from scutl_signer.core import Signer
from scutl_signer.network import (
    BLESSED,
    PermanentError,
    resolve_binding,
    select_offer,
)
from scutl_signer.state import StateDir

SEPOLIA = BLESSED["eip155:84532"]
MAINNET = BLESSED["eip155:8453"]


def _mainnet_offer(amount="2000000", asset=MAINNET.usdc_address,
                   extra_name="USD Coin"):
    """AgentMail-shaped v2 quote: five chains, Base first."""
    def req(network, asset_):
        return {"scheme": "exact", "network": network, "amount": amount,
                "asset": asset_, "payTo": "0x" + "6e" * 20,
                "maxTimeoutSeconds": 300,
                "extra": {"name": extra_name, "version": "2"}}
    return {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": "https://api.example.test/v0/inboxes",
                     "description": "inbox", "mimeType": "application/json"},
        "accepts": [
            req("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "EPj" + "x" * 40),
            req("eip155:43114", "0x" + "b9" * 20),
            req("eip155:8453", asset_=asset),
            req("eip155:137", "0x" + "3c" * 20),
        ],
        "extensions": {},
    }


class MockChain:
    def usdc_balance(self, address):
        return Decimal("10")

    def tx_status(self, tx_hash):
        return "confirmed"


@pytest.fixture
def signer(tmp_path):
    state = StateDir(tmp_path / "wallet")
    s = Signer(state=state, chain=MockChain(), facilitator=None,
               binding=SEPOLIA)
    approvals.grant(state, "keygen")
    s.keygen(cap_per_tx=Decimal("0.10"), cap_daily=Decimal("1.00"))
    return s


# -- select_offer ------------------------------------------------------

def test_select_offer_picks_blessed_network_only():
    offer = select_offer(_mainnet_offer(), MAINNET)
    assert offer["version"] == 2
    assert offer["requirements"]["network"] == "eip155:8453"
    assert offer["amount_atomic"] == 2000000


def test_select_offer_rejects_when_network_absent():
    quote = _mainnet_offer()
    quote["accepts"] = [r for r in quote["accepts"]
                        if r["network"] != "eip155:8453"]
    with pytest.raises(PermanentError, match="no exact-scheme offer"):
        select_offer(quote, MAINNET)


def test_select_offer_rejects_wrong_asset():
    with pytest.raises(PermanentError, match="not blessed USDC"):
        select_offer(_mainnet_offer(asset="0x" + "de" * 20), MAINNET)


def test_select_offer_rejects_wrong_eip712_domain():
    with pytest.raises(PermanentError, match="domain name"):
        select_offer(_mainnet_offer(extra_name="USDC"), MAINNET)


def test_select_offer_v1_quote_still_parses():
    quote = {"x402Version": 1, "accepts": [{
        "scheme": "exact", "network": "base-sepolia",
        "maxAmountRequired": "10000", "payTo": "0x" + "20" * 20,
        "asset": SEPOLIA.usdc_address, "maxTimeoutSeconds": 120,
        "extra": {"name": "USDC", "version": "2"}}]}
    offer = select_offer(quote, SEPOLIA)
    assert offer["version"] == 1
    assert offer["amount_atomic"] == 10000


# -- v2 payload build --------------------------------------------------

def test_authorize_v2_payload_shape_and_signature(signer):
    quote = _mainnet_offer(amount="10000", asset=SEPOLIA.usdc_address,
                           extra_name="USDC")
    quote["accepts"][2]["network"] = "eip155:84532"
    offer = select_offer(quote, SEPOLIA)
    auth = signer.authorize("pid-v2", offer["pay_to"], Decimal("0.01"),
                            offer=offer)
    assert auth["x402_version"] == 2
    payload = json.loads(base64.b64decode(auth["header"]))
    assert payload["x402Version"] == 2
    assert payload["accepted"] == offer["requirements"]
    a = payload["payload"]["authorization"]
    # v2 wire: value and validity window are strings
    assert a["value"] == "10000"
    assert isinstance(a["validAfter"], str)
    # signature recovers to the wallet key over the binding's domain
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
        "domain": {"name": SEPOLIA.eip712_name, "version": "2",
                   "chainId": SEPOLIA.chain_id,
                   "verifyingContract": SEPOLIA.usdc_address},
        "message": {**a, "value": int(a["value"]),
                    "validAfter": int(a["validAfter"]),
                    "validBefore": int(a["validBefore"])},
    }
    recovered = Account.recover_message(
        encode_typed_data(full_message=typed),
        signature=payload["payload"]["signature"])
    assert recovered == signer.address()


# -- zero-amount identity calls ---------------------------------------

def test_zero_amount_costs_no_cap_headroom(signer):
    quote = _mainnet_offer(amount="0", asset=SEPOLIA.usdc_address,
                           extra_name="USDC")
    quote["accepts"][2]["network"] = "eip155:84532"
    offer = select_offer(quote, SEPOLIA)
    signer.authorize("pid-zero", offer["pay_to"], Decimal("0"), offer=offer)
    assert signer.state.cap_exposure() == Decimal("0")
    rec = signer.record_settled("pid-zero", offer["pay_to"], Decimal("0"),
                                None)
    assert rec["chain_status"] == "no-tx"
    assert rec["tx"] == ""


# -- network bindings --------------------------------------------------

def test_keygen_pins_network(signer):
    assert signer.state.load_network() == "eip155:84532"
    # a fresh Signer on the same state resolves the pinned binding
    again = Signer(state=signer.state, chain=MockChain(), facilitator=None)
    assert again.binding is SEPOLIA


def test_mainnet_pay_refused_without_facilitator(tmp_path):
    state = StateDir(tmp_path / "w")
    s = Signer(state=state, chain=MockChain(), binding=MAINNET)
    approvals.grant(state, "keygen")
    s.keygen(cap_per_tx=Decimal("5"), cap_daily=Decimal("5"))
    with pytest.raises(PermanentError, match="merchant-settles"):
        s.pay("pid-mainnet", "0x" + "6e" * 20, Decimal("2"))


def test_unblessed_network_refused():
    with pytest.raises(PermanentError, match="not blessed"):
        resolve_binding("eip155:1")
