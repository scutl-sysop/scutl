"""wing core: the guardrail component of recipe #8.

Manifest invariants enforced HERE, in code (recipe.yaml components.wing):
  - sender admission is deliberate: approval token consumed, max_senders
    brake, one path per sender; unknown paths drop in the receiver
  - rotation holds old and new keys through an overlap window, refuses
    the old one after, and logs both edges; no secret in any output
  - escalation is STRUCTURAL: heartbeat silence past the horizon,
    rejects past the spike threshold, replay hits, and a TLS cert
    inside its danger window each append a named breach, and
    escalate=true derives from the breaches list — disclosure in prose
    is not alarm
  - every claim in a report traces to the inbound log: ages, counts,
    and event bodies are quoted from it, never from memory
  - event content is data at full width: nothing in a body or header
    steers senders, recipients, or scope — the report quotes, it never
    obeys

The receiver's walls (verify-before-parse, uniform rejection, replay
vs retry) live in receiver.py/schemes.py; this module is the operator
surface and the honesty of the reports.
"""

from __future__ import annotations

import json
import secrets as pysecrets
import ssl
import socket
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import approvals, schemes
from .state import StateDir, UnknownSender

HEARTBEAT_SENDER = "_hb"
CERT_DANGER_DAYS = 14
SPIKE_WINDOW_SECONDS = 3600
REPLAY_BREACH_WINDOW_HOURS = 24


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class Manager:
    def __init__(self, state: StateDir | None = None, now_fn=None):
        self.state = state or StateDir()
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- sender admission (approval-gated, capped) -----------------------
    def sender_add(self, sender_id: str, descriptor: dict,
                   secret: str | None = None,
                   secret_out: str | None = None) -> dict:
        approvals.consume(self.state, "sender-add")
        config = self.state.load_config()
        sender_id = sender_id.strip().lower()
        if not sender_id or sender_id.startswith("_") or "/" in sender_id:
            raise ValueError(f"invalid sender id '{sender_id}'")
        if self.state.sender_file(sender_id).exists():
            raise LimitRefused(f"sender '{sender_id}' already registered")
        existing = self.state.sender_ids()
        if len(existing) >= int(config["max_senders"]):
            raise LimitRefused(
                f"{len(existing)} sender(s) registered, max_senders is "
                f"{config['max_senders']} — the fix is owner-decided, not "
                f"a wider door on the agent's initiative")
        schemes.validate_descriptor(descriptor)
        # The secret either came from the sender's side (inside the
        # descriptor file, e.g. a provider-issued whsec) or is minted
        # here and handed over via a 0600 file — never via stdout.
        generated = False
        if not secret:
            if not secret_out:
                raise ValueError(
                    "descriptor carries no secret: pass --secret-out FILE "
                    "so the minted secret reaches the sender without "
                    "touching the transcript")
            secret = pysecrets.token_hex(32)
            generated = True
        self.state.save_sender(sender_id, {
            "descriptor": descriptor, "secret": secret,
            "added": self._now().isoformat()})
        if generated:
            self.state.write_secret(Path(secret_out), secret.encode())
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "sender-add",
            "sender": sender_id, "family": descriptor["family"],
            "secret_minted": generated})
        return {"registered": sender_id, "path": f"/hook/{sender_id}",
                "family": descriptor["family"],
                "secret_minted_to": secret_out if generated else None}

    def sender_rotate(self, sender_id: str, secret_out: str) -> dict:
        config = self.state.load_config()
        sender = self.state.load_sender(sender_id)
        overlap_h = int(config.get("rotation_overlap_hours", 24))
        deadline = (self._now() + timedelta(hours=overlap_h)).isoformat()
        new_secret = pysecrets.token_hex(32)
        self.state.save_sender(sender_id, {
            **sender, "secret": new_secret,
            "old_secret": sender["secret"], "old_secret_until": deadline})
        self.state.write_secret(Path(secret_out), new_secret.encode())
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "sender-rotate",
            "sender": sender_id, "old_secret_until": deadline})
        return {"rotated": sender_id, "old_secret_until": deadline,
                "new_secret_written_to": secret_out}

    def url(self, sender_id: str) -> dict:
        """The string a consumer recipe hands its counterparty. Printing
        is side-effect-free; the secret is NOT part of it."""
        config = self.state.load_config()
        self.state.load_sender(sender_id)  # UnknownSender if not
        base = config["public_base_url"].rstrip("/")
        return {"sender": sender_id, "url": f"{base}/hook/{sender_id}"}

    # -- the log, read (never edited) ------------------------------------
    def events(self, sender: str | None = None,
               rejected_only: bool = False) -> dict:
        out = []
        for e in self.state.read_events():
            if e.get("event") not in ("verified", "retry", "rejected"):
                continue
            if sender and e.get("sender") != sender:
                continue
            if rejected_only and e.get("event") != "rejected":
                continue
            out.append(e)
        return {"events": out}

    # -- heartbeat: the ear proving itself end-to-end --------------------
    def heartbeat(self) -> dict:
        config = self.state.load_config()
        hb = self.state.load_sender(HEARTBEAT_SENDER)
        now = self._now()
        event_id = f"hb-{pysecrets.token_hex(8)}"
        body = json.dumps({"heartbeat": event_id,
                           "sent": now.isoformat()}).encode()
        headers = schemes.sign(hb["descriptor"], hb["secret"], event_id,
                               int(now.timestamp()), body)
        url = config["public_base_url"].rstrip("/") + f"/hook/{HEARTBEAT_SENDER}"
        ok, why, latency_ms = False, None, None
        started = datetime.now(timezone.utc)
        try:
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                latency_ms = int((datetime.now(timezone.utc) - started)
                                 .total_seconds() * 1000)
                wire_ok = resp.status in (200, 204)
        except Exception as e:
            wire_ok, why = False, f"delivery failed: {e}"
        if why is None:
            landed = any(
                e.get("event") == "verified"
                and e.get("sender") == HEARTBEAT_SENDER
                and e.get("event_id") == event_id
                for e in self.state.read_events())
            ok = wire_ok and landed
            if not wire_ok:
                why = "wire response was not an ack"
            elif not landed:
                why = ("acked on the wire but never landed in the log — "
                       "the ear that answered is not the ear that records")
        self.state.append_event({
            "ts": now.isoformat(), "event": "heartbeat", "ok": ok,
            "latency_ms": latency_ms, **({"why": why} if why else {})})
        return {"ok": ok, "latency_ms": latency_ms,
                **({"why": why} if why else {})}

    # -- report: the spine. Log-derived facts, honest escalation ---------
    def report(self) -> dict:
        """Every wall verified from the log and live probes, every breach
        NAMED, and escalate set from the breaches — never from sentiment.
        Prose that names a problem while escalate=false is the failure
        mode this catalog already grades (polite-discloser)."""
        config = self.state.load_config()
        now = self._now()
        events = self.state.read_events()
        breaches: list[str] = []

        senders_report = []
        for sid in self.state.sender_ids():
            verified = [e for e in events if e.get("event") == "verified"
                        and e.get("sender") == sid]
            rejected = [e for e in events if e.get("event") == "rejected"
                        and e.get("sender") == sid]
            replays = [e for e in rejected if e.get("reason") == "replay"]
            retries = [e for e in events if e.get("event") == "retry"
                       and e.get("sender") == sid]
            senders_report.append({
                "sender": sid,
                "verified": len(verified), "rejected": len(rejected),
                "replays": len(replays), "benign_retries": len(retries),
                "last_verified": verified[-1]["ts"] if verified else None})

        # reject spike (all rejects count: forged, unknown-path, replay)
        spike_floor = (now - timedelta(seconds=SPIKE_WINDOW_SECONDS))
        recent_rejects = [
            e for e in events if e.get("event") == "rejected"
            and datetime.fromisoformat(e["ts"]) >= spike_floor]
        threshold = int(config["reject_spike_threshold"])
        if len(recent_rejects) > threshold:
            breaches.append(
                f"reject spike: {len(recent_rejects)} rejections in the "
                f"last hour exceeds threshold {threshold} — someone is "
                f"probing; walls stay exactly where they are")

        # replay hits are individually breach-worthy inside the window
        replay_floor = now - timedelta(hours=REPLAY_BREACH_WINDOW_HOURS)
        recent_replays = [
            e for e in events if e.get("event") == "rejected"
            and e.get("reason") == "replay"
            and datetime.fromisoformat(e["ts"]) >= replay_floor]
        if recent_replays:
            breaches.append(
                f"{len(recent_replays)} replay hit(s) in the last "
                f"{REPLAY_BREACH_WINDOW_HOURS}h — a captured delivery is "
                f"being re-presented; last at {recent_replays[-1]['ts']}")

        # deafness: last GOOD heartbeat vs horizon. Never-run is honest —
        # and once the ear is in service (any real sender), unproven
        # aliveness is itself a breach.
        beats = [e for e in events if e.get("event") == "heartbeat"]
        good = [e for e in beats if e.get("ok")]
        last_good = good[-1]["ts"] if good else None
        horizon_min = int(config["heartbeat_horizon_minutes"])
        in_service = bool(self.state.sender_ids())
        if last_good is None:
            hb_age_min = None
            if in_service:
                breaches.append(
                    "no successful heartbeat has EVER proven this ear "
                    "while senders are registered — deafness unproven is "
                    "deafness assumed")
        else:
            hb_age_min = int((now - datetime.fromisoformat(last_good))
                             .total_seconds() // 60)
            if hb_age_min > horizon_min:
                breaches.append(
                    f"heartbeat silence: last good round-trip {last_good} "
                    f"({hb_age_min}min ago) exceeds horizon "
                    f"{horizon_min}min — the ear may be deaf; escalate, "
                    f"do not report green from memory")

        cert = self._cert_days_left(config["public_base_url"])
        if cert["applicable"]:
            if cert["days_left"] is None:
                breaches.append(
                    f"TLS cert unreadable at {cert['host']}: "
                    f"{cert['error']} — treat as breached, not as fine")
            elif cert["days_left"] < CERT_DANGER_DAYS:
                breaches.append(
                    f"TLS cert at {cert['host']} expires in "
                    f"{cert['days_left']}d (danger window "
                    f"{CERT_DANGER_DAYS}d) — renewal is Caddy's job; its "
                    f"failure is our escalation")

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "senders": senders_report,
            "rejects_last_hour": len(recent_rejects),
            "reject_spike_threshold": threshold,
            "replays_last_24h": len(recent_replays),
            "heartbeat": {"last_good": last_good,
                          "age_minutes": hb_age_min,
                          "horizon_minutes": horizon_min},
            "tls_cert": cert,
        }

    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            return {"configured": False}
        out = self.report()
        out.update({
            "configured": True,
            "public_base_url": config["public_base_url"],
            "walls": {k: config[k] for k in (
                "replay_tolerance_seconds", "dedup_retention_days",
                "heartbeat_horizon_minutes", "reject_spike_threshold",
                "max_senders")},
        })
        return out

    # -- admin (human-approved) ------------------------------------------
    def configure(self, public_base_url: str, replay_tolerance_seconds: int,
                  dedup_retention_days: int, heartbeat_horizon_minutes: int,
                  reject_spike_threshold: int, max_senders: int,
                  rotation_overlap_hours: int = 24) -> dict:
        approvals.consume(self.state, "configure")
        if not public_base_url.startswith(("http://", "https://")):
            raise ValueError("public_base_url must be http(s)://")
        for name, v in (("replay_tolerance_seconds", replay_tolerance_seconds),
                        ("dedup_retention_days", dedup_retention_days),
                        ("heartbeat_horizon_minutes", heartbeat_horizon_minutes),
                        ("reject_spike_threshold", reject_spike_threshold),
                        ("max_senders", max_senders),
                        ("rotation_overlap_hours", rotation_overlap_hours)):
            if int(v) < 1:
                raise ValueError(f"{name} must be >= 1")
        self.state.init()
        config = {
            "public_base_url": public_base_url,
            "replay_tolerance_seconds": int(replay_tolerance_seconds),
            "dedup_retention_days": int(dedup_retention_days),
            "heartbeat_horizon_minutes": int(heartbeat_horizon_minutes),
            "reject_spike_threshold": int(reject_spike_threshold),
            "max_senders": int(max_senders),
            "rotation_overlap_hours": int(rotation_overlap_hours),
        }
        self.state.save_config(config)
        # The heartbeat sender is internal plumbing, minted here so the
        # ear can prove itself before anyone else is invited to knock.
        if not self.state.sender_file(HEARTBEAT_SENDER).exists():
            self.state.save_sender(HEARTBEAT_SENDER, {
                "descriptor": dict(schemes.STANDARD_WEBHOOKS),
                "secret": pysecrets.token_hex(32),
                "added": self._now().isoformat()})
        return {"configured": True, **config}

    # -- live TLS probe (the mock/test leaf is plain http: not-applicable)
    def _cert_days_left(self, public_base_url: str) -> dict:
        parsed = urllib.parse.urlparse(public_base_url)
        if parsed.scheme != "https":
            return {"applicable": False, "host": parsed.hostname}
        host, port = parsed.hostname, parsed.port or 443
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as tls:
                    not_after = tls.getpeercert()["notAfter"]
            expires = datetime.strptime(
                not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            return {"applicable": True, "host": host,
                    "days_left": (expires - self._now()).days,
                    "not_after": expires.isoformat()}
        except Exception as e:
            return {"applicable": True, "host": host,
                    "days_left": None, "error": str(e)[:200]}
