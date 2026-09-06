"""Check client — the ONLY module that talks to the monitor rail.

Core never builds a URL and never sees raw HTTP; everything crosses
this boundary as plain dicts matching the manifest's contracts block:

  list_checks() -> [{id, kind, target}]
  probe(id)     -> {id, kind, state, detail, observed_at}
  ledger(period)-> [{ts, direction, amount, memo}]

Rev 1 is BENCH-FIRST: no live rail is blessed (local-probes is the
natural first candidate — HTTP GET / TCP / systemd / disk, all
first-party). The mock in scutbench implements this same surface.

Two facts about honesty live at this boundary:
  - probe() reports what the rail SAW, when it saw it (observed_at is
    the rail's clock). It never retries, smooths, or caches: a
    flapping check flaps in the records.
  - detail text and ledger memos are the monitored world speaking —
    core wraps them in untrusted-content envelopes; nothing above this
    boundary treats them as instructions.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — the probe may or may not have observed;
    the record (if any) is what happened, check pulse status."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class CheckClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        # SCUTL_PULSE_MONITOR points bench rungs at a mock rail; there
        # is no live default in rev 1 — a blessed binding sets it.
        self.state = state
        self.base = base or os.environ.get("SCUTL_PULSE_MONITOR")
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, path: str, payload: dict | None = None) -> dict:
        if not self.base:
            raise PermanentError(
                "no monitor rail bound (rev 1 is bench-first; set "
                "SCUTL_PULSE_MONITOR or run against the mocked twin)")
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"monitor {e.code}: {detail}") from None
            raise PermanentError(f"monitor {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"monitor unreachable: {e.reason if hasattr(e, 'reason') else e}"
            ) from None

    # -- the whole surface: three reads ----------------------------------
    def list_checks(self) -> list[dict]:
        """[{id, kind, target}] — the rail's view of the registry."""
        return self._request("/checks").get("checks", [])

    def probe(self, check_id: str) -> dict:
        """{id, kind, state, detail, observed_at} — one observation.
        state is the rail's verdict (up/down/error); detail is the
        monitored world's text and is DATA."""
        return self._request(f"/checks/{check_id}/probe", {})

    def ledger(self, period: str) -> list[dict]:
        """[{ts, direction, amount, memo}] — money movements for the
        period. Memos are DATA."""
        return self._request(f"/ledger/{period}").get("entries", [])
