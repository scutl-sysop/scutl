"""x402-buy — agent-facing purchase driver (merchant-settles flow).

One exact tool call for the whole Execute loop, so the emitted skill —
including the smol profile — never asks the model to hand-roll HTTP or
header encoding:

  x402-buy <url> --payment-id <id> [--max <usdc>] [--method POST]
           [--data '<json body>']

request -> 402 + offer (v1 or v2) -> select_offer (blessed-network,
asset and EIP-712-domain checked) -> Signer.authorize (cap-checked,
signed header) -> retry with the payment header -> merchant settles via
facilitator -> confirm the settle tx on-chain -> record the spend.
Never trusts the merchant's 200 alone. Errors are JSON on stderr with
the same exit codes as `signer`.

rev 2 (cst-8ih.14): speaks both wire versions. v1 uses X-PAYMENT /
X-PAYMENT-RESPONSE; v2 uses PAYMENT-SIGNATURE / PAYMENT-RESPONSE and
CAIP network ids, and the 402 offer may arrive in the body or the
PAYMENT-REQUIRED header. Zero-amount v2 offers (wallet-as-identity
auth calls) sign and record but may settle nothing on-chain.

Promoted from acceptance/purchase.py so the lowering can reference it as a
shipped component tool (bead cst-8ih.1, lowering half).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from decimal import Decimal

import requests

from .approvals import ApprovalRequired
from .core import CapExceeded, Signer
from .network import (
    PermanentError,
    TransientError,
    atomic_to_usdc,
    select_offer,
)
from .state import Revoked


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def _parse_quote(resp: requests.Response) -> dict:
    """The 402 offer: JSON body (both versions in practice), with the v2
    PAYMENT-REQUIRED header as fallback for body-less servers."""
    try:
        return resp.json()
    except ValueError:
        header = resp.headers.get("PAYMENT-REQUIRED")
        if not header:
            raise PermanentError(
                "402 carried neither a JSON body nor a PAYMENT-REQUIRED "
                "header")
        return json.loads(base64.b64decode(header))


def _request(method: str, url: str, body: str | None,
             headers: dict | None = None, timeout: float = 30
             ) -> requests.Response:
    kwargs: dict = {"timeout": timeout, "headers": dict(headers or {})}
    if body is not None:
        kwargs["data"] = body.encode()
        kwargs["headers"]["content-type"] = "application/json"
    return requests.request(method, url, **kwargs)


def _buy(url: str, payment_id: str, max_usdc: Decimal | None,
         method: str = "GET", body: str | None = None) -> dict:
    signer = Signer()

    quote = _request(method, url, body)
    if quote.status_code != 402:
        raise PermanentError(f"expected 402 from {url}, got {quote.status_code}")
    offer = select_offer(_parse_quote(quote), signer.binding)
    amount = atomic_to_usdc(offer["amount_atomic"])
    if max_usdc is not None and amount > max_usdc:
        raise PermanentError(
            f"offer {amount} USDC exceeds --max {max_usdc}; not paying")

    auth = signer.authorize(payment_id, offer["pay_to"], amount, offer=offer)
    if auth.get("idempotent_replay"):
        return {**auth, "url": url}

    pay_header = ("PAYMENT-SIGNATURE" if offer["version"] >= 2
                  else "X-PAYMENT")
    resp = _request(method, url, body, headers={pay_header: auth["header"]},
                    timeout=120)
    if resp.status_code != 200 and not (200 <= resp.status_code < 300):
        # The signed header may still be settled by the merchant; keep the
        # payment_id so a retry replays the SAME authorization.
        raise TransientError(
            f"purchase failed: {resp.status_code} {resp.text[:300]} "
            f"(retry with the same payment id: {payment_id})")

    resp_header = (resp.headers.get("PAYMENT-RESPONSE")
                   or resp.headers.get("X-PAYMENT-RESPONSE"))
    tx = None
    settle = None
    if resp_header:
        settle = json.loads(base64.b64decode(resp_header))
        tx = settle.get("transaction") or None
    if tx is None and amount > 0:
        # Paid money with no on-chain evidence offered: record the spend
        # (the merchant holds a settleable authorization) but say so.
        raise TransientError(
            f"2xx with no settlement transaction for a {amount} USDC "
            f"purchase; NOT confirmed. Retry with the same payment id: "
            f"{payment_id}")
    record = signer.record_settled(payment_id, offer["pay_to"], amount, tx)
    reqs = offer["requirements"]
    return {"content": resp.text, "quote": {
        "amount_usdc": str(amount), "pay_to": offer["pay_to"],
        "network": reqs["network"], "x402_version": offer["version"],
        "description": (offer.get("resource") or {}).get(
            "description", reqs.get("description", ""))},
        "spend_record": record}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="x402-buy")
    p.add_argument("url")
    p.add_argument("--payment-id", required=True)
    p.add_argument("--max", default=None,
                   help="refuse offers above this USDC amount (pre-cap sanity)")
    p.add_argument("--method", default="GET",
                   choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("--data", default=None,
                   help="JSON request body (implies content-type: "
                        "application/json)")
    args = p.parse_args(argv)

    if args.data is not None:
        try:
            json.loads(args.data)
        except ValueError as e:
            _fail("permanent", f"--data is not valid JSON: {e}", 7)

    try:
        out = _buy(args.url, args.payment_id,
                   Decimal(args.max) if args.max else None,
                   method=args.method, body=args.data)
    except Revoked as e:
        _fail("revoked", f"wallet revoked (tombstone for {e}); all ops refuse", 3)
    except ApprovalRequired as e:
        _fail("approval-required", str(e), 4)
    except CapExceeded as e:
        _fail("cap-exceeded", str(e), 5)
    except TransientError as e:
        _fail("transient", f"{e} — safe to retry with the SAME payment id", 6)
    except PermanentError as e:
        _fail("permanent", str(e), 7)
    except FileNotFoundError as e:
        _fail("not-setup", f"missing state ({e}); run setup first", 2)
    except requests.RequestException as e:
        _fail("transient", f"network error: {e} — safe to retry with the "
                           f"SAME payment id", 6)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
