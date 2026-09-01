"""Live bindings: real HTTP for the local face, an UptimeRobot-shaped
API for the outside face.

HONEST STATUS (recipe.yaml bindings.live.probes-pending): these
bindings are written against the v3 surface as byte-checked
anonymously on 2026-08-30 (bearer auth; 401 {"message":"Invalid
token."}) plus the vendor's public docs. The read-side field names
for evidence freshness (last_observed_at here) are the FIRST question
for the live account — if the v3 monitor read hides a last-check
timestamp, the deafness wall must ride the v2 logs surface instead,
and this module changes. Nothing in core depends on these bindings;
the component tests and the SMUTbench twin implement the rails
contract directly, so rev 1 is reference-green without a live
account (the manifest's $0 claim).

Custody note carried into code: the prober main key is account-wide
by the vendor's design. It is read from the custody file at call
time, held only in the request, and appears in no return value, no
exception text, and no log line.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .rails import LocalRail, ProberRail, ProberUnreachable, RailError
from .state import StateDir

TIMEOUT_S = 20


class LiveLocal(LocalRail):
    """Fetch the target's health path from this host. The serial is
    read from an 'X-Beacon-Serial' header or a 'serial=<epoch>' token
    in the body — services adopt whichever face is cheaper; absence is
    simply reported (ok requires freshness, so no serial = not ok)."""

    def __init__(self, state: StateDir):
        self.state = state

    def fetch(self, target: dict) -> dict:
        req = urllib.request.Request(
            target["url"], headers={"User-Agent": "scutl-beacon/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                body = resp.read(1 << 20).decode("utf-8", "replace")
                status = resp.status
                serial = resp.headers.get("X-Beacon-Serial")
        except urllib.error.HTTPError as e:
            body = ""
            status = e.code
            serial = None
        except Exception:
            return {"status_code": None, "sentinel_present": False,
                    "serial_age_seconds": None}
        if serial is None and "serial=" in body:
            serial = body.split("serial=", 1)[1].split()[0].strip('",')
        age = None
        if serial:
            try:
                age = max(0, int(time.time()) - int(float(serial)))
            except ValueError:
                age = None
        return {"status_code": status,
                "sentinel_present": target["sentinel"] in body,
                "serial_age_seconds": age}


class LiveProber(ProberRail):
    """UptimeRobot v3-shaped bearer REST. The base URL is a config
    parameter, never a constant (manifest: the self-host escape hatch
    stays one config change away). Reads are batched: read_all is one
    GET — the free tier allows 10 req/min."""

    def __init__(self, state: StateDir):
        self.state = state

    def _base(self) -> str:
        return self.state.load_config()["prober_api_base"].rstrip("/")

    def _key(self) -> str:
        if not self.state.api_key_file.exists():
            raise ProberUnreachable(
                f"no prober key at {self.state.api_key_file} — the "
                f"signup is a Conway consent (free, cardless)")
        return self.state.api_key_file.read_text().strip()

    def _call(self, method: str, path: str, payload: dict | None = None):
        req = urllib.request.Request(
            self._base() + path,
            data=(json.dumps(payload).encode() if payload is not None
                  else None),
            headers={"Authorization": f"Bearer {self._key()}",
                     "Content-Type": "application/json",
                     "User-Agent": "scutl-beacon/0.1"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ProberUnreachable(
                    f"prober rejected the key (HTTP {e.code})") from None
            raise RailError(f"prober HTTP {e.code} on {path}") from None
        except OSError as e:
            raise ProberUnreachable(f"prober dark: {e}") from None

    # NOTE: field mappings below are the probes-pending surface; the
    # shapes are asserted here so a mismatch fails LOUD, not quietly.
    def upsert(self, name, url, keyword, cadence_seconds):
        existing = {m["name"]: m for m in self.read_all()}
        if name in existing:
            m = existing[name]
            self._call("PATCH", f"/monitors/{m['monitor_id']}", {
                "url": url, "keywordValue": keyword,
                "interval": int(cadence_seconds)})
            return {"monitor_id": m["monitor_id"], "created": False}
        # v3 live shape (first contact 2026-08-31, cst-u3eu acceptance):
        # keywordType is ALERT_NOT_EXISTS ("up" while the sentinel is
        # present), keywordCaseType and timeout are required.
        out = self._call("POST", "/monitors", {
            "type": "keyword", "friendlyName": name, "url": url,
            "keywordType": "ALERT_NOT_EXISTS",
            "keywordCaseType": "CaseSensitive",
            "keywordValue": keyword, "timeout": 30,
            "interval": int(cadence_seconds)})
        mid = str(out.get("id") or out.get("monitor", {}).get("id") or "")
        if not mid:
            raise RailError("monitor create returned no id — v3 create "
                            "shape differs from the recon's read of it "
                            "(probes-pending #2); refusing to guess")
        return {"monitor_id": mid, "created": True}

    def _v2_last_checks(self) -> dict:
        """monitor_id -> newest check ISO timestamp, from the v2
        response_times surface. v3's lastCheckedAt is null on every
        surface observed live (list and per-monitor, 2026-09-01,
        cst-2din), so the deafness byte rides v2 — and only with an
        explicit start/end window: the default window lags hours.
        Best-effort: a v2 failure returns {} and the wall degrades
        honestly to prober-deaf, never crashes the verify."""
        base = self._base()
        if "/v3" not in base:
            return {}
        now = int(time.time())
        form = urllib.parse.urlencode({
            "api_key": self._key(), "format": "json",
            "response_times": "1",
            "response_times_start_date": str(now - 3600),
            "response_times_end_date": str(now)}).encode()
        req = urllib.request.Request(
            base.replace("/v3", "/v2") + "/getMonitors", data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": "scutl-beacon/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                out = json.loads(resp.read().decode() or "{}")
        except (OSError, ValueError):
            return {}
        checks = {}
        for m in out.get("monitors", []):
            rts = m.get("response_times") or []
            if rts:
                newest = max(r["datetime"] for r in rts)
                checks[str(m["id"])] = datetime.fromtimestamp(
                    newest, tz=timezone.utc).isoformat()
        return checks

    def read_all(self):
        out = self._call("GET", "/monitors")
        rows = out.get("monitors") or out.get("data") or []
        v2_checks = self._v2_last_checks() if any(
            not (m.get("lastCheckedAt") or m.get("last_checked_at"))
            for m in rows) else {}
        result = []
        for m in rows:
            result.append({
                "monitor_id": str(m.get("id")),
                "name": m.get("friendlyName") or m.get("friendly_name"),
                "config": {
                    "url": m.get("url"),
                    "keyword": m.get("keywordValue") or m.get("keyword_value"),
                    "cadence_seconds": int(m.get("interval") or 0),
                },
                # v3 status vocabulary observed live: UP / DOWN /
                # STARTED (pre-first-check) / PAUSED
                "state": ("up" if str(m.get("status", "")).upper()
                          in ("UP", "STARTED", "2") else "down"),
                # probes-pending #1: THE byte the deafness wall rides
                "last_observed_at": (m.get("lastCheckedAt")
                                     or m.get("last_checked_at")
                                     or v2_checks.get(str(m.get("id")))),
                "paused": str(m.get("status", "")).upper() in ("0", "PAUSED"),
                "incidents": m.get("incidents", []),
            })
        return result

    def pause(self, monitor_id):
        self._call("PATCH", f"/monitors/{monitor_id}", {"status": "paused"})

    def delete(self, monitor_id):
        self._call("DELETE", f"/monitors/{monitor_id}")
