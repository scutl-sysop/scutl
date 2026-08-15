"""Vultr API v2 client — the ONLY module that talks to the provider.

Core never builds a URL and never sees the raw HTTP layer; everything
crosses this boundary as plain dicts. The API key is read from state per
request and appears in exactly one place: the Authorization header. It is
never interpolated into URLs, log lines, or exception messages — error
text is built from status codes and response bodies only (Vultr does not
echo the key back).

Mocks in tests/ implement this same surface; the manifest's contracts
block is the source of truth for the ops and failure modes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal

API_BASE = "https://api.vultr.com/v2"
DEFAULT_OS_ID = 2136  # Debian 12 x64


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class VultrClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        # SCUTL_PROV_API points ladder rungs at the mock provider; the
        # live default is the real API. Same pattern as the facilitator
        # override in recipes #1/#2.
        self.state = state
        self.base = base or os.environ.get("SCUTL_PROV_API") or API_BASE
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        key = self.state.load_api_key()
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"vultr {e.code}: {detail}") from None
            raise PermanentError(f"vultr {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(f"vultr unreachable: {e.reason if hasattr(e, 'reason') else e}") from None

    # -- account / plans -----------------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/account").get("account", {})

    def plans(self) -> list[dict]:
        """[{id, hourly_cost (Decimal, USD), monthly_cost, ...}] — Vultr
        reports monthly_cost; hourly derives at 730 h/mo, quantized up so
        the ceiling check never rounds a price under the limit."""
        out = []
        for p in self._request("GET", "/plans?per_page=500").get("plans", []):
            monthly = Decimal(str(p.get("monthly_cost", "0")))
            p["hourly_cost"] = (monthly / 730).quantize(Decimal("0.0001"))
            out.append(p)
        return out

    # -- instances -----------------------------------------------------
    def create_instance(self, plan: str, region: str, label: str,
                        os_id: int = DEFAULT_OS_ID,
                        user_data: str | None = None) -> dict:
        body = {"plan": plan, "region": region, "label": label, "os_id": os_id}
        if user_data is not None:
            import base64
            body["user_data"] = base64.b64encode(user_data.encode()).decode()
        return self._request("POST", "/instances", body).get("instance", {})

    def list_instances(self) -> list[dict]:
        return self._request("GET", "/instances?per_page=500").get("instances", [])

    def get_instance(self, instance_id: str) -> dict:
        return self._request("GET", f"/instances/{instance_id}").get("instance", {})

    def destroy_instance(self, instance_id: str) -> None:
        self._request("DELETE", f"/instances/{instance_id}")

    # -- DNS (one domain: the delegated subzone) -------------------------
    def list_records(self, domain: str) -> list[dict]:
        return self._request(
            "GET", f"/domains/{domain}/records?per_page=500").get("records", [])

    def create_record(self, domain: str, name: str, rtype: str, value: str,
                      ttl: int = 300) -> dict:
        return self._request("POST", f"/domains/{domain}/records", {
            "name": name, "type": rtype, "data": value, "ttl": ttl,
        }).get("record", {})

    def delete_record(self, domain: str, record_id: str) -> None:
        self._request("DELETE", f"/domains/{domain}/records/{record_id}")
