"""Channel client — the ONLY module that talks to the messenger provider.

Core never builds a URL and never sees raw HTTP; everything crosses this
boundary as plain dicts. The credential is read from state per request
and appears in exactly one place: the Authorization header — never in
URLs, log lines, or exception messages.

SINGLE-RECIPIENT BY CONSTRUCTION: send() takes a peer id, and core
passes exactly one value there — the configured owner_peer_id. No other
call site exists; there is no broadcast, no group op, no lookup that
returns a different peer to message. A blessed provider binding must
fit this shape without widening it; widening it is a rev bump and a
safety-spine conversation first.

Rev 1 is BENCH-FIRST: no live provider is blessed (telegram-bot is the
natural first candidate; signal-cli deferred behind it — identity
custody is heavier). The mock in scutbench implements this same
surface; the manifest's contracts block is the source of truth for ops
and failure modes. Inbound records carry the provider's peer identity
verdict: peer_id is who the PROVIDER says is speaking, verified is
whether it vouches for that — display names never appear at this layer
as identity.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — a send that hit one may STILL have
    delivered; check herald status (the log) before any retry."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class ChannelClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        # SCUTL_HERALD_CHANNEL points bench rungs at a mock provider;
        # there is no live default in rev 1 — a blessed binding sets it.
        self.state = state
        self.base = base or os.environ.get("SCUTL_HERALD_CHANNEL")
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, path: str, payload: dict | None = None) -> dict:
        if not self.base:
            raise PermanentError(
                "no channel endpoint bound (rev 1 is bench-first; set "
                "SCUTL_HERALD_CHANNEL or run against the mocked twin)")
        cred = self.state.load_credential()
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers={"Authorization": f"Bearer {cred}",
                     **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 404:
                raise PermanentError(f"message-gone: {detail}") from None
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"channel {e.code}: {detail}") from None
            raise PermanentError(f"channel {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"channel unreachable: {e.reason if hasattr(e, 'reason') else e}"
            ) from None

    # -- the whole surface: one write, two reads -------------------------
    def send(self, peer_id: str, body: str) -> dict:
        """{message_id, delivered_at} — deliver one message to peer_id.
        Core's only call site passes the configured owner; that this
        parameter exists at all is for the provider boundary, not for
        callers — nothing above core can reach it."""
        return self._request("/send", {"peer_id": peer_id, "body": body})

    def list(self) -> list[dict]:
        """[{id, peer_id, verified, date}] — inbound headers only.
        peer_id is the PROVIDER's account identity for the speaker;
        verified is whether the provider vouches for it. Display names
        are content, not identity, and do not appear here."""
        return self._request("/messages").get("messages", [])

    def read(self, message_id: str) -> dict:
        """{id, peer_id, verified, date, body} — one inbound message."""
        return self._request(f"/messages/{message_id}")
