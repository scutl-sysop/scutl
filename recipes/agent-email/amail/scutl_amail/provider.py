"""Provider client — the ONLY module that talks to the inbox rail.

Core never builds a URL and never sees raw HTTP; everything crosses this
boundary as plain dicts. The credential appears in exactly one place:
the Authorization header — never in URLs, log lines, or exceptions.

Rev 1 is BENCH-FIRST: no live provider is blessed by default. The wire
shape below is the manifest's provider contract (AgentMail-shaped but
provider-agnostic); the smutbench mock implements this same surface,
and a live AgentMail binding is an adapter over the same ops.

Identity at this layer: every inbound message carries the PROVIDER's
authentication verdict as labels (e.g. 'unauthenticated', 'spam',
'blocked' — absence meaning the sender's domain authenticated).
Display names are content, not identity, and core treats them so.

Idempotent sends: send()/reply() take the caller's idempotency key and
pass it as the Idempotency-Key header. Provider semantics (from the
manifest contract): first use sends and records; a retry with the same
key returns the original {message_id, thread_id} and sends nothing; the
same key with different content is 409 (surfaced as PermanentError so
an accidental reuse fails loudly).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — a send that hit one may STILL have gone
    out; retry with the SAME send_id or hand it to reconcile."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class MailProvider:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        # SCUTL_AMAIL_PROVIDER points bench rungs at a mock provider;
        # there is no live default in rev 1 — a blessed binding sets it.
        self.state = state
        self.base = base or os.environ.get("SCUTL_AMAIL_PROVIDER")
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def _request(self, path: str, payload: dict | None = None,
                 headers: dict | None = None) -> dict:
        if not self.base:
            raise PermanentError(
                "no provider endpoint bound (rev 1 is bench-first; set "
                "SCUTL_AMAIL_PROVIDER or run against the mocked twin)")
        cred = self.state.load_credential()
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers={"Authorization": f"Bearer {cred}",
                     **({"Content-Type": "application/json"} if data else {}),
                     **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"provider {e.code}: {detail}") from None
            raise PermanentError(f"provider {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"provider unreachable: {e.reason if hasattr(e, 'reason') else e}"
            ) from None

    # -- writes (all idempotency-keyed) ---------------------------------
    def send(self, key: str, to: list[str], subject: str, text: str) -> dict:
        """{message_id, thread_id}"""
        return self._request("/send",
                             {"to": to, "subject": subject, "text": text},
                             {"Idempotency-Key": key})

    def reply(self, key: str, message_id: str, text: str) -> dict:
        """{message_id, thread_id} — provider addresses the reply from
        the target message's reply-to/from; core has already verified
        those recipients against the allowlist."""
        return self._request(f"/messages/{message_id}/reply",
                             {"text": text}, {"Idempotency-Key": key})

    def create_draft(self, key: str, to: list[str], subject: str,
                     text: str) -> dict:
        """{draft_id} — parked for human release; nothing sent."""
        return self._request("/drafts",
                             {"to": to, "subject": subject, "text": text},
                             {"Idempotency-Key": key})

    def update_labels(self, message_id: str, add: list[str],
                      remove: list[str]) -> dict:
        return self._request(f"/messages/{message_id}/labels",
                             {"add": add, "remove": remove})

    # -- reads ---------------------------------------------------------
    def list_threads(self, unreplied_only: bool = False) -> list[dict]:
        """[{thread_id, subject, labels, last_ts, last_from}]"""
        q = "?unreplied=1" if unreplied_only else ""
        return self._request(f"/threads{q}").get("threads", [])

    def get_thread(self, thread_id: str) -> dict:
        """{thread_id, messages: [{message_id, from, to, reply_to, cc,
        labels, ts, subject, text, html, attachments}]} — messages in
        chronological order; labels carry the provider's auth verdict."""
        return self._request(f"/threads/{thread_id}")

    def history(self) -> list[dict]:
        """[{message_id, direction: sent|received, ts, to, from}] — the
        provider's own record; the reconciliation surface."""
        return self._request("/history").get("events", [])
