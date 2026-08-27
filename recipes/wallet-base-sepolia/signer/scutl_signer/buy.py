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

rev 3 (cst-rjba, recipe x402v2): --probe reports an offer without paying
(no signer, no state — the look-before-you-buy tool), --binding reports
the active network binding, and --field NAME=VALUE lowers the offer's
bazaar input schema into the request body — values from the caller only,
merchant defaults/examples never fill anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from decimal import Decimal

import requests

from .approvals import ApprovalRequired
from .bazaar import BazaarError, extract_input, lower_request
from .core import CapExceeded, Signer
from .network import (
    DEFAULT_NETWORK,
    NetworkBinding,
    PermanentError,
    TransientError,
    atomic_to_usdc,
    resolve_binding,
    select_offer,
)
from .state import Revoked, StateDir


def _fail(kind: str, message: str, code: int = 1) -> None:
    print(json.dumps({"error": kind, "message": message}), file=sys.stderr)
    sys.exit(code)


def _parse_quote(resp: requests.Response) -> dict:
    """The 402 offer: JSON body (both versions in practice), with the v2
    PAYMENT-REQUIRED header as fallback for servers whose body is empty
    or carries no offer (some send a placeholder `{}` body)."""
    try:
        quote = resp.json()
    except ValueError:
        quote = None
    if quote and quote.get("accepts"):
        return quote
    header = resp.headers.get("PAYMENT-REQUIRED")
    if not header:
        if quote is not None:
            return quote
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


def _ambient_binding() -> NetworkBinding:
    """The wallet's active binding, or the default when no wallet state
    exists yet — probe and binding-report must work pre-ceremony (recipe
    x402v2: 'the look-before-you-buy tool; free and safe')."""
    try:
        caip = StateDir().load_network() or DEFAULT_NETWORK
    except Exception:
        caip = DEFAULT_NETWORK
    return resolve_binding(caip)


def _host(url: str | None) -> str | None:
    if not url:
        return None
    return url.split("//", 1)[-1].split("/", 1)[0] or None


def _probe(url: str, method: str, body: str | None) -> dict:
    """Fetch and report a 402 offer WITHOUT paying: no signer, no state
    writes, no payment header — the only side effect is the request the
    merchant already answers unauthenticated."""
    binding = _ambient_binding()
    resp = _request(method, url, body)
    if resp.status_code != 402:
        raise PermanentError(
            f"expected 402 from {url}, got {resp.status_code} — "
            f"not an x402-priced resource (or already authorized)")
    quote = _parse_quote(resp)
    accepts = quote.get("accepts") or []
    offers = [{"network": r.get("network"), "amount": r.get("amount",
               r.get("maxAmountRequired")), "payTo": r.get("payTo"),
               "asset": r.get("asset")} for r in accepts]
    try:
        chosen = select_offer(quote, binding)
        selected: dict | None = {
            "network": chosen["requirements"].get("network"),
            "amount_usdc": str(atomic_to_usdc(chosen["amount_atomic"])),
            "pay_to": chosen["pay_to"]}
        refusal = None
    except (PermanentError, KeyError) as e:
        selected, refusal = None, str(e)
    resource_url = (quote.get("resource") or {}).get("url")
    bazaar = extract_input(quote.get("extensions") or {})
    return {
        "probe": True,
        "x402_version": quote.get("x402Version", 1),
        "binding": binding.caip,
        "offers": offers,
        "selected": selected,
        "refusal": refusal,
        # proxy disclosure: who serves the 402 vs who the resource says
        # it is. Differing hosts = a reseller in the trust chain.
        "quote_host": _host(url),
        "resource_host": _host(resource_url),
        "proxy": (_host(resource_url) is not None
                  and _host(resource_url) != _host(url)),
        "bazaar": (None if bazaar is None else {
            "method": bazaar.get("method"),
            "bodyType": bazaar.get("bodyType"),
            "fields": sorted((bazaar.get("body") or {}))}),
    }


def _binding_report() -> dict:
    b = _ambient_binding()
    return {"binding": b.caip, "legacy_name": b.legacy_name,
            "chain_id": b.chain_id, "usdc_address": b.usdc_address,
            "eip712_name": b.eip712_name, "testnet": b.testnet}


def _buy(url: str, payment_id: str, max_usdc: Decimal | None,
         method: str = "GET", body: str | None = None,
         fields: dict[str, str] | None = None) -> dict:
    signer = Signer()

    quote = _request(method, url, body)
    if quote.status_code != 402:
        raise PermanentError(f"expected 402 from {url}, got {quote.status_code}")
    offer = select_offer(_parse_quote(quote), signer.binding)
    if fields is not None:
        bazaar_input = extract_input(offer["extensions"])
        if bazaar_input is None:
            raise PermanentError(
                "--field given but the offer carries no bazaar input schema")
        try:
            lowered = lower_request(bazaar_input, fields)
        except BazaarError as e:
            raise PermanentError(str(e)) from e
        if lowered.method != method:
            raise PermanentError(
                f"offer's bazaar schema says method {lowered.method}, "
                f"request used {method}; re-run with --method {lowered.method}")
        body = lowered.body
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
    p.add_argument("url", nargs="?")
    p.add_argument("--payment-id")
    p.add_argument("--probe", action="store_true",
                   help="fetch and report the 402 offer without paying "
                        "(no signer, no state, no payment header)")
    p.add_argument("--binding", action="store_true",
                   help="report the active network binding and exit")
    p.add_argument("--field", action="append", default=None,
                   metavar="NAME=VALUE",
                   help="body field for the offer's bazaar schema; "
                        "repeatable. Values come from YOU — schema "
                        "defaults/examples never fill anything")
    p.add_argument("--max", default=None,
                   help="refuse offers above this USDC amount (pre-cap sanity)")
    p.add_argument("--method", default="GET",
                   choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("--data", default=None,
                   help="JSON request body (implies content-type: "
                        "application/json)")
    args = p.parse_args(argv)

    if args.binding:
        print(json.dumps(_binding_report()))
        return
    if args.url is None:
        _fail("permanent", "url is required (except with --binding)", 7)

    if args.data is not None:
        try:
            json.loads(args.data)
        except ValueError as e:
            _fail("permanent", f"--data is not valid JSON: {e}", 7)

    fields: dict[str, str] | None = None
    if args.field is not None:
        if args.data is not None:
            _fail("permanent", "--field and --data are mutually exclusive", 7)
        fields = {}
        for item in args.field:
            name, sep, value = item.partition("=")
            if not sep or not name:
                _fail("permanent",
                      f"--field {item!r} is not NAME=VALUE", 7)
            fields[name] = value
        # bazaar bodies are non-GET; a body-bearing probe request needs a
        # JSON placeholder so the merchant answers with the right offer.
        if args.method != "GET" and args.data is None:
            args.data = "{}"

    try:
        if args.probe:
            print(json.dumps(_probe(args.url, args.method, args.data)))
            return
        if not args.payment_id:
            _fail("permanent", "--payment-id is required to buy", 7)
        out = _buy(args.url, args.payment_id,
                   Decimal(args.max) if args.max else None,
                   method=args.method, body=args.data, fields=fields)
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
