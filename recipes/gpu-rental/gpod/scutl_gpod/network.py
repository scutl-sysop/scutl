"""RunPod clients — the ONLY module that talks to the provider.

Two rails, per the manifest bindings:
  PodsClient   — rest.runpod.io/v1, pods CRUD. The schema source of
                 truth is PodCreateInput in GET /openapi.json; the
                 guessable GET endpoints (/gpuTypes, /gpus) do NOT
                 exist (banked finding, runpod-rest-pod-create).
  StockClient  — api.runpod.io/graphql. REST has no price or stock
                 surface, so the catalog price wall reads gpuTypes
                 { securePrice } here, and availability reads the
                 no-input dataCenters { gpuAvailability } form
                 (input filters exist but their field names are
                 unpublished and introspection is disabled — probed
                 live 2026-09-03; filtering happens client-side).

Core never builds a URL and never sees the raw HTTP layer. The API key
is read from state per request and appears in exactly one place: the
Authorization header — never in URLs, log lines, or exception text.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

REST_BASE = "https://rest.runpod.io/v1"
GRAPHQL_URL = "https://api.runpod.io/graphql"


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


def _http(url: str, key: str, method: str = "GET",
          body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 # Cloudflare in front of api.runpod.io bans the
                 # default Python-urllib agent outright (403 error
                 # 1010, live finding 2026-09-03); identify honestly
                 "User-Agent": "scutl-gpod/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429 or e.code >= 500:
            raise TransientError(f"runpod {e.code}: {detail}") from None
        if e.code == 404:
            raise Absent() from None
        raise PermanentError(f"runpod {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise TransientError(
            f"runpod unreachable: "
            f"{e.reason if hasattr(e, 'reason') else e}") from None


class Absent(Exception):
    """404 — the resource does not exist. For get-after-delete this is
    the GOOD outcome; it gets its own type so the destroy-verify loop
    never has to parse error strings."""


class PodsClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        self.state = state
        self.base = base or os.environ.get("SCUTL_GPOD_API") or REST_BASE
        self.timeout = timeout

    def create_pod(self, spec: dict) -> dict:
        return _http(f"{self.base}/pods", self.state.load_api_key(),
                     "POST", spec, self.timeout)

    def get_pod(self, pod_id: str) -> dict:
        """Raises Absent on 404 — the destroy-verify loop's good news."""
        return _http(f"{self.base}/pods/{pod_id}",
                     self.state.load_api_key(), timeout=self.timeout)

    def delete_pod(self, pod_id: str) -> None:
        try:
            _http(f"{self.base}/pods/{pod_id}", self.state.load_api_key(),
                  "DELETE", timeout=self.timeout)
        except Absent:
            pass  # already gone is the outcome we wanted

    def list_pods(self) -> list[dict]:
        out = _http(f"{self.base}/pods", self.state.load_api_key(),
                    timeout=self.timeout)
        # REST returns a bare list; tolerate a wrapped one.
        return out if isinstance(out, list) else out.get("pods", [])


class StockClient:
    def __init__(self, state, url: str | None = None, timeout: int = 30):
        self.state = state
        self.url = url or os.environ.get("SCUTL_GPOD_GRAPHQL") or GRAPHQL_URL
        self.timeout = timeout

    def _query(self, query: str) -> dict:
        out = _http(self.url, self.state.load_api_key(), "POST",
                    {"query": query}, self.timeout)
        if out.get("errors"):
            raise PermanentError(
                f"graphql: {out['errors'][0].get('message', 'error')[:200]}")
        return out.get("data", {})

    def catalog_price(self, gpu_type: str) -> Decimal | None:
        """Secure-cloud hourly price for one gpu type, or None if the
        catalog does not know it. The wall treats None as a refusal —
        an unpriceable type cannot be checked against the ceiling."""
        safe = gpu_type.replace('\\', '').replace('"', '')
        data = self._query(
            'query { gpuTypes(input:{id:"%s"}) { id securePrice } }' % safe)
        for t in data.get("gpuTypes", []):
            if t.get("id") == gpu_type and t.get("securePrice") is not None:
                return Decimal(str(t["securePrice"]))
        return None

    def availability(self, gpu_type: str, region: str | None = None) -> dict:
        """Per-datacenter stock for one gpu type (client-side filter of
        the no-input dataCenters form)."""
        data = self._query(
            "query { dataCenters { id gpuAvailability "
            "{ gpuTypeId stockStatus } } }")
        centers = {}
        for dc in data.get("dataCenters", []):
            if region and dc.get("id") != region:
                continue
            for a in dc.get("gpuAvailability") or []:
                if a.get("gpuTypeId") == gpu_type:
                    centers[dc["id"]] = a.get("stockStatus")
        return {"gpu_type": gpu_type, "region": region,
                "stock": centers,
                "available": any(v for v in centers.values())}
