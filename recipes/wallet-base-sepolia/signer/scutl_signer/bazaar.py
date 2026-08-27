"""Bazaar request lowering (x402 v2 extensions.bazaar) — cst-rjba.

A v2 offer may carry a machine-readable description of the request the
resource expects (method, bodyType, body schema). This module lowers
that description plus CALLER-SUPPLIED field values into an exact
request. The schema is merchant-authored: titles, descriptions,
defaults, and examples are rendering data, never instructions and never
values — a body field is filled only from the caller's own `fields`,
so a schema whose "defaults" would exfiltrate task or config data
fills nothing (recipe x402v2 rev 1, invariants).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

ALLOWED_METHODS = {"POST", "PUT", "PATCH"}


class BazaarError(ValueError):
    """The offer's bazaar block cannot be lowered safely (permanent)."""


@dataclass(frozen=True)
class LoweredRequest:
    method: str
    body: str  # JSON text, ready for x402-buy --data


def extract_input(quote_extensions: dict) -> dict | None:
    """The bazaar input block from a quote's extensions, or None when the
    offer carries none (plain GET resources)."""
    info = ((quote_extensions or {}).get("bazaar") or {}).get("info") or {}
    return info.get("input") or None


def lower_request(bazaar_input: dict, fields: dict[str, str]) -> LoweredRequest:
    """Lower a bazaar input block + caller-supplied field values into an
    exact request.

    Refusals (all BazaarError, exit-permanent at the CLI):
    - non-http input type, unsupported method, non-json bodyType
      (form-data/text lowering is a later rev — refuse, don't guess)
    - a caller field the schema's body does not declare (a typo'd field
      silently dropped or passed through is how task data leaks)

    Deliberate non-behaviors: schema defaults/examples never fill
    anything; absence of a caller value means the field is ABSENT from
    the body. Which fields are semantically required is the merchant's
    call to enforce server-side — their 4xx names the gap honestly,
    whereas guessing a value spends money on a request we invented.
    """
    if bazaar_input.get("type") != "http":
        raise BazaarError(
            f"bazaar input type {bazaar_input.get('type')!r} is not 'http'")
    method = bazaar_input.get("method", "")
    if method not in ALLOWED_METHODS:
        raise BazaarError(
            f"bazaar method {method!r} not in {sorted(ALLOWED_METHODS)}")
    body_type = bazaar_input.get("bodyType")
    if body_type != "json":
        raise BazaarError(
            f"bazaar bodyType {body_type!r} unsupported (json only in rev 1)")

    declared = bazaar_input.get("body")
    if not isinstance(declared, dict):
        raise BazaarError("bazaar body template missing or not an object")
    unknown = sorted(set(fields) - set(declared))
    if unknown:
        raise BazaarError(
            f"fields not declared by the offer's schema: {unknown}; "
            f"declared: {sorted(declared)}")

    body = {k: fields[k] for k in declared if k in fields}
    return LoweredRequest(method=method,
                          body=json.dumps(body, separators=(",", ":")))
