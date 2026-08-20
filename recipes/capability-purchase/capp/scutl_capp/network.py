"""Vendor client — the ONLY module that talks to the API vendor.

Core never builds a URL and never sees raw HTTP; everything crosses this
boundary as plain dicts. The API key is read from state per request and
appears in exactly one place: the Authorization header — never in URLs,
log lines, or exception messages.

Rev 1 is BENCH-FIRST: no live vendor is blessed yet (the x402-v2 client
work gates that), so this client is the shape a blessed vendor must fit,
and the mock in smutbench/capp implements the same surface. The
manifest's contracts block is the source of truth for ops and failure
modes.

The one wire subtlety worth naming: purchase() is the only op whose
RESPONSE carries the secret. Core hands that response straight to
StateDir.write_secret and strips the key before anything is returned,
logged, or raised.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry (after checking state)."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class VendorClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        # SCUTL_CAPP_API points bench rungs at a mock vendor; there is no
        # live default in rev 1 — a blessed vendor binding sets it.
        self.state = state
        self.base = base or os.environ.get("SCUTL_CAPP_API")
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None,
                 key: str | None = None) -> dict:
        if not self.base:
            raise PermanentError(
                "no vendor endpoint bound (rev 1 is bench-first; set "
                "SCUTL_CAPP_API or run against the mocked twin)")
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"vendor {e.code}: {detail}") from None
            raise PermanentError(f"vendor {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"vendor unreachable: {e.reason if hasattr(e, 'reason') else e}"
            ) from None

    # -- catalog / account ---------------------------------------------
    def plans(self) -> list[dict]:
        """[{id, price_usd, quota_calls, ...}] — the vendor's price list."""
        return self._request("GET", "/plans").get("plans", [])

    def purchase(self, plan_id: str) -> dict:
        """Buy a plan. Returns {purchase_id, plan, price_usd, quota_calls,
        api_key} — the ONE response that carries the secret."""
        return self._request("POST", "/purchase", {"plan": plan_id})

    def purchases(self) -> list[dict]:
        """Purchases the vendor has recorded for this payer — the
        reconciliation surface for ack-lost purchases."""
        return self._request("GET", "/purchases").get("purchases", [])

    # -- metered use ----------------------------------------------------
    def call(self, key: str, query: str) -> dict:
        return self._request("POST", "/call", {"query": query}, key=key)

    def usage(self, key: str) -> dict:
        """{used, quota} — the VENDOR's counter, compared against ours."""
        return self._request("GET", "/usage", key=key)
