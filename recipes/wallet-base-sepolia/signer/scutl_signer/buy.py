"""x402-buy — agent-facing purchase driver (merchant-settles flow).

One exact tool call for the whole Execute loop, so the emitted skill —
including the smol profile — never asks the model to hand-roll HTTP or
header encoding:

  x402-buy <url> --payment-id <id> [--max <usdc>]

GET -> 402 + requirements -> Signer.authorize (cap-checked, signed header)
-> GET with X-PAYMENT -> merchant settles via facilitator -> confirm the
settle tx on-chain -> record the spend. Never trusts the merchant's 200
alone. Errors are JSON on stderr with the same exit codes as `signer`.

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
from .network import PermanentError, TransientError, atomic_to_usdc
from .state import Revoked


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def _buy(url: str, payment_id: str, max_usdc: Decimal | None) -> dict:
    signer = Signer()

    quote = requests.get(url, timeout=30)
    if quote.status_code != 402:
        raise PermanentError(f"expected 402 from {url}, got {quote.status_code}")
    reqs = quote.json()["accepts"][0]
    if reqs["network"] != "base-sepolia":
        raise PermanentError(f"wrong network in offer: {reqs['network']}")
    amount = Decimal(atomic_to_usdc(int(reqs["maxAmountRequired"])))
    if max_usdc is not None and amount > max_usdc:
        raise PermanentError(
            f"offer {amount} USDC exceeds --max {max_usdc}; not paying")

    auth = signer.authorize(payment_id, reqs["payTo"], amount)
    if auth.get("idempotent_replay"):
        return {**auth, "url": url}

    resp = requests.get(url, headers={"X-PAYMENT": auth["header"]}, timeout=120)
    if resp.status_code != 200:
        # The signed header may still be settled by the merchant; keep the
        # payment_id so a retry replays the SAME authorization.
        raise TransientError(
            f"purchase failed: {resp.status_code} {resp.text[:300]} "
            f"(retry with the same payment id: {payment_id})")

    settle = json.loads(base64.b64decode(resp.headers["X-PAYMENT-RESPONSE"]))
    record = signer.record_settled(
        payment_id, reqs["payTo"], amount, settle["transaction"])
    return {"content": resp.text, "quote": {
        "amount_usdc": str(amount), "pay_to": reqs["payTo"],
        "description": reqs.get("description", "")}, "spend_record": record}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="x402-buy")
    p.add_argument("url")
    p.add_argument("--payment-id", required=True)
    p.add_argument("--max", default=None,
                   help="refuse offers above this USDC amount (pre-cap sanity)")
    args = p.parse_args(argv)

    try:
        out = _buy(args.url, args.payment_id,
                   Decimal(args.max) if args.max else None)
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
