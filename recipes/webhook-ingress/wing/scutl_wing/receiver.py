"""The receiver: every delivery lands here, and the walls run in order.

handle_delivery() is pure with respect to the wire — it takes the path,
headers, and RAW body bytes, and returns (http_status, response_body).
The HTTP wrapper below is deliberately thin; the bench's mocked twin
drives handle_delivery directly, playing senders and network itself.

Wire discipline (manifest invariants):
  - the response is UNIFORM across every rejection reason — unknown
    path, missing header, bad signature, stale/future timestamp,
    replay, expired old key — one status, empty body, no oracle;
  - accepted deliveries (fresh or benign retry) ack fast with an empty
    body — no echo, no detail;
  - the log gets the full granular story, append-only: rejects are
    evidence, not noise. Secrets appear in no log line; bodies of
    UNVERIFIED deliveries are logged as hashes only (an attacker's
    payload does not get to occupy verbatim space in the record).

Replay vs retry (code rule, stated in the manifest): the same event id
arriving again within replay_tolerance_seconds of its FIRST arrival is
a sender retry — acked, deduplicated to one recorded event with a
delivery count. Arriving later than that, it is a replay — rejected and
counted as a breach. For body-mac schemes (no timestamp in the MAC)
this arrival-clock rule and the durable ledger are the only replay
walls there are.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from . import schemes
from .state import StateDir, UnknownSender

REJECT = (404, b"")
ACCEPT = (204, b"")
HOOK_PREFIX = "/hook/"


def _seen(state: StateDir, sender_id: str, event_id: str,
          retention_days: int, now: datetime) -> dict | None:
    """Newest verified event for this (sender, id) inside the retention
    window; the dedup ledger IS the inbound log — nothing to drift."""
    horizon = now.timestamp() - retention_days * 86400
    for e in reversed(state.read_events()):
        if (e.get("event") == "verified" and e.get("sender") == sender_id
                and e.get("event_id") == event_id):
            arrived = datetime.fromisoformat(e["ts"]).timestamp()
            return e if arrived >= horizon else None
    return None


def handle_delivery(state: StateDir, path: str, headers: dict,
                    raw_body: bytes, now: datetime | None = None
                    ) -> tuple[int, bytes]:
    now = now or datetime.now(timezone.utc)
    try:
        config = state.load_config()
    except Exception:
        return REJECT  # unconfigured ear answers nothing

    def log_reject(reason: str, sender: str | None, event_id: str | None,
                   detail: str = "") -> tuple[int, bytes]:
        state.append_event({
            "ts": now.isoformat(), "event": "rejected", "path": path,
            "sender": sender, "reason": reason, "event_id": event_id,
            "body_sha256": hashlib.sha256(raw_body).hexdigest(),
            **({"detail": detail} if detail else {})})
        return REJECT

    if not path.startswith(HOOK_PREFIX):
        return log_reject("unknown-path", None, None)
    sender_id = path[len(HOOK_PREFIX):].strip("/")
    try:
        sender = state.load_sender(sender_id)
    except UnknownSender:
        return log_reject("unknown-path", None, None)

    # Rotation overlap: the old secret verifies until its deadline, then
    # is dead — but the wire response for an expired key is the uniform
    # reject, distinguishable only in the log.
    secrets = [sender["secret"]]
    old, until = sender.get("old_secret"), sender.get("old_secret_until")
    old_alive = bool(old and until and now.isoformat() <= until)
    if old_alive:
        secrets.append(old)

    tolerance = int(config["replay_tolerance_seconds"])
    outcome = schemes.verify(sender["descriptor"], secrets, headers,
                             raw_body, int(now.timestamp()), tolerance)
    if not outcome["mac_valid"]:
        reason = outcome["reason"]
        # An expired old key produces bad-signature; name it honestly in
        # the log when the dead key WOULD have matched.
        if reason == "bad-signature" and old and not old_alive:
            retry = schemes.verify(sender["descriptor"], [old], headers,
                                   raw_body, int(now.timestamp()), tolerance)
            if retry["mac_valid"]:
                reason = "rotated-key-expired"
        return log_reject(reason, sender_id, outcome["event_id"])

    # Dedup ledger BEFORE the timestamp walls: an exact replay carries
    # its original (now stale) timestamp, and a seen id is a replay
    # finding — the ledger, not the clock, is the authority on "again".
    prior = _seen(state, sender_id, outcome["event_id"],
                  int(config["dedup_retention_days"]), now)
    if prior is not None:
        first = datetime.fromisoformat(prior["ts"])
        if (now - first).total_seconds() <= tolerance:
            # benign sender retry: ack, dedup to one event, count it
            state.append_event({
                "ts": now.isoformat(), "event": "retry",
                "sender": sender_id, "event_id": outcome["event_id"]})
            return ACCEPT
        return log_reject("replay", sender_id, outcome["event_id"],
                          detail=f"first seen {prior['ts']}")
    if not outcome["verified"]:
        # never-seen but outside the skew window: a delayed or forged
        # first delivery, named by direction
        return log_reject(outcome["reason"], sender_id,
                          outcome["event_id"])

    # Verified and fresh: the body is recorded VERBATIM (byte-true via
    # base64, readable via utf-8-with-replacement) — and that is ALL
    # that happens. A valid signature authenticates the sender, not the
    # demand; no code path here acts on content.
    state.append_event({
        "ts": now.isoformat(), "event": "verified", "sender": sender_id,
        "event_id": outcome["event_id"], "signed_ts": outcome["ts"],
        "body": raw_body.decode("utf-8", errors="replace"),
        "body_b64": base64.b64encode(raw_body).decode()})
    return ACCEPT


def make_server(state: StateDir, port: int, bind: str = "127.0.0.1"):
    """Thin loopback HTTP wrapper: Caddy is the only public listener
    (paid-service ingress component); this never binds beyond loopback."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            status, body = handle_delivery(
                state, self.path, dict(self.headers), raw)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # the inbound log is the record
            pass

    return HTTPServer((bind, port), Handler)


def serve(state: StateDir, port: int, bind: str = "127.0.0.1") -> None:
    make_server(state, port, bind).serve_forever()
