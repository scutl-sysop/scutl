"""Mailbox client — the ONLY module that talks to the mail provider.

Core never builds a URL and never sees raw HTTP; everything crosses this
boundary as plain dicts. The credential is read from state per request
and appears in exactly one place: the Authorization header — never in
URLs, log lines, or exception messages.

READ-ONLY BY CONSTRUCTION: the surface is list() and read(id). There is
no send, no delete, no move, no mark — not gated, not stubbed, absent.
A blessed provider binding must fit this shape without widening it;
widening it is a rev bump and a safety-spine conversation first.

Rev 1 is BENCH-FIRST: no live provider is blessed (AgentMail deferred
per docs/agentmail-x402-recon.md; generic IMAP deferred further — the
first live inbox must be agent-owned). The mock in scutbench implements
this same surface; the manifest's contracts block is the source of
truth for ops and failure modes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry (after checking state)."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class MailboxClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        # SCUTL_SIFT_MAILBOX points bench rungs at a mock provider; there
        # is no live default in rev 1 — a blessed provider binding sets it.
        self.state = state
        self.base = base or os.environ.get("SCUTL_SIFT_MAILBOX")
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, path: str) -> dict:
        if not self.base:
            raise PermanentError(
                "no mailbox endpoint bound (rev 1 is bench-first; set "
                "SCUTL_SIFT_MAILBOX or run against the mocked twin)")
        cred = self.state.load_credential()
        req = urllib.request.Request(
            f"{self.base}{path}",
            headers={"Authorization": f"Bearer {cred}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 404:
                raise PermanentError(f"message-gone: {detail}") from None
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"mailbox {e.code}: {detail}") from None
            raise PermanentError(f"mailbox {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"mailbox unreachable: {e.reason if hasattr(e, 'reason') else e}"
            ) from None

    # -- the whole surface: two reads ----------------------------------
    def list(self) -> list[dict]:
        """[{id, from, subject, date, auth_result}] — headers only.
        auth_result is the PROVIDER's sender authentication verdict
        (e.g. dmarc-pass / dmarc-fail / none); the claimed From is a
        claim, this field is the evidence."""
        return self._request("/messages").get("messages", [])

    def read(self, message_id: str) -> dict:
        """{id, from, subject, date, auth_result, body} — one message."""
        return self._request(f"/messages/{message_id}")
