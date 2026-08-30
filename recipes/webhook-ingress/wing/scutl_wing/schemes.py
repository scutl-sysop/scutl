"""The verify engine: one interpreter over per-sender scheme descriptors.

The 2026 survey (docs/webhook-ingress-recon.md) found three incompatible
wire families, so the verifier is a DESCRIPTOR interpreted by one engine,
never sender-specific code and never a fall-through across descriptors.

Descriptor fields (stored per sender in state, alongside the secrets):
  family        "timestamp-mac" | "body-mac"
                timestamp-mac signs a timestamp into the canonical string
                (Standard Webhooks, Stripe, Slack) — the skew window is a
                real wall. body-mac signs the body alone (GitHub) — the
                durable dedup ledger is the ONLY replay wall.
  sig_format    "prefixed-list": header is whitespace-separated candidate
                signatures, each stripped of sig_prefix
                (Standard Webhooks "v1,..."; GitHub "sha256=..."; Slack
                "v0=...").
                "kv-list": header is comma-separated k=v pairs; the
                timestamp comes from key "t" and candidates from key
                "v1" (Stripe).
  sig_header    header carrying the signature(s)
  sig_prefix    prefix stripped from each candidate ("" for none)
  ts_header     header carrying the unix timestamp (timestamp-mac with
                sig_format prefixed-list; null for kv-list, where t=
                inside the sig header is the source)
  id_header     header carrying the sender's event id (null -> dedup
                falls back to the body hash)
  canonical     template for the signed content: "{id}.{ts}.{body}"
                (Standard Webhooks), "{ts}.{body}" (Stripe),
                "v0:{ts}:{body}" (Slack), "{body}" (GitHub)
  encoding      "base64" | "hex" for the MAC comparison

Verification is HMAC-SHA256 over the RAW body bytes, compared
constant-time, against every provided secret (current, plus the old one
inside a rotation overlap window) and every candidate signature — any
match verifies. Missing required headers reject; nothing here ever
tries a second descriptor.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

REQUIRED = ("family", "sig_format", "sig_header", "canonical", "encoding")


class BadDescriptor(ValueError):
    """Descriptor missing required fields or naming unknown modes."""


def validate_descriptor(d: dict) -> dict:
    for field in REQUIRED:
        if not d.get(field):
            raise BadDescriptor(f"descriptor lacks '{field}'")
    if d["family"] not in ("timestamp-mac", "body-mac"):
        raise BadDescriptor(f"unknown family '{d['family']}'")
    if d["sig_format"] not in ("prefixed-list", "kv-list"):
        raise BadDescriptor(f"unknown sig_format '{d['sig_format']}'")
    if d["encoding"] not in ("base64", "hex"):
        raise BadDescriptor(f"unknown encoding '{d['encoding']}'")
    if d["family"] == "timestamp-mac" and d["sig_format"] == "prefixed-list" \
            and not d.get("ts_header"):
        raise BadDescriptor("timestamp-mac with prefixed-list needs ts_header")
    if "{body}" not in d["canonical"]:
        raise BadDescriptor("canonical must include {body}")
    return d


def _candidates(descriptor: dict, headers: dict) -> tuple[list[str], str | None]:
    """(candidate signatures, timestamp-string-or-None) from the headers.
    Header lookup is case-insensitive (HTTP), values used byte-exact."""
    low = {k.lower(): v for k, v in headers.items()}
    raw = low.get(descriptor["sig_header"].lower())
    if raw is None:
        return [], None
    prefix = descriptor.get("sig_prefix") or ""
    if descriptor["sig_format"] == "kv-list":
        ts, sigs = None, []
        for part in raw.split(","):
            k, _, v = part.strip().partition("=")
            if k == "t":
                ts = v
            elif k == "v1":
                sigs.append(v)
        return sigs, ts
    sigs = [c[len(prefix):] for c in raw.split()
            if not prefix or c.startswith(prefix)]
    ts = None
    if descriptor.get("ts_header"):
        ts = low.get(descriptor["ts_header"].lower())
    return sigs, ts


def verify(descriptor: dict, secrets: list[str], headers: dict,
           raw_body: bytes, now_ts: int, tolerance: int) -> dict:
    """One delivery against one sender's descriptor. Returns
      {verified, reason, event_id, ts}
    reason on failure is for the LOG only — the wire response is uniform
    regardless (receiver's job). No descriptor fall-through exists by
    construction: the caller resolved exactly one sender from the path.
    """
    low = {k.lower(): v for k, v in headers.items()}
    sigs, ts_raw = _candidates(descriptor, headers)
    event_id = (low.get(descriptor["id_header"].lower())
                if descriptor.get("id_header") else None) \
        or hashlib.sha256(raw_body).hexdigest()

    if not sigs:
        return {"verified": False, "mac_valid": False,
                "reason": "missing-header", "event_id": event_id, "ts": None}

    ts_val = None
    if descriptor["family"] == "timestamp-mac":
        if not ts_raw or not ts_raw.strip().lstrip("-").isdigit():
            return {"verified": False, "mac_valid": False,
                    "reason": "missing-header", "event_id": event_id,
                    "ts": None}
        ts_val = int(ts_raw)

    wire_id = (low.get(descriptor["id_header"].lower(), "")
               if descriptor.get("id_header") else "")
    canonical = descriptor["canonical"] \
        .replace("{id}", wire_id).replace("{ts}", ts_raw or "")
    prefix, _, suffix = canonical.partition("{body}")
    signed = prefix.encode() + raw_body + suffix.encode()

    matched = False
    for secret in secrets:
        mac = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
        want = (base64.b64encode(mac).decode()
                if descriptor["encoding"] == "base64" else mac.hex())
        for candidate in sigs:
            if hmac.compare_digest(want, candidate):
                matched = True
    if not matched:
        return {"verified": False, "mac_valid": False,
                "reason": "bad-signature", "event_id": event_id,
                "ts": ts_val}

    # MAC valid; the timestamp wall applies in BOTH directions. The
    # caller checks its dedup ledger BEFORE honoring these reasons: an
    # exact replay carries its original (now stale) timestamp, and a
    # seen id is a replay finding, not a skew finding.
    if ts_val is not None:
        if ts_val < now_ts - tolerance:
            return {"verified": False, "mac_valid": True,
                    "reason": "stale-timestamp", "event_id": event_id,
                    "ts": ts_val}
        if ts_val > now_ts + tolerance:
            return {"verified": False, "mac_valid": True,
                    "reason": "future-timestamp", "event_id": event_id,
                    "ts": ts_val}
    return {"verified": True, "mac_valid": True, "reason": None,
            "event_id": event_id, "ts": ts_val}


def sign(descriptor: dict, secret: str, event_id: str, ts: int,
         raw_body: bytes) -> dict:
    """Produce headers for a delivery under this descriptor — used by the
    heartbeat (the ear proving itself end-to-end) and by tests. The twin
    in the bench uses the same helper to play honest and hostile senders."""
    canonical = descriptor["canonical"] \
        .replace("{id}", event_id).replace("{ts}", str(ts))
    prefix, _, suffix = canonical.partition("{body}")
    signed = prefix.encode() + raw_body + suffix.encode()
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    sig = (base64.b64encode(mac).decode()
           if descriptor["encoding"] == "base64" else mac.hex())
    headers = {}
    if descriptor["sig_format"] == "kv-list":
        headers[descriptor["sig_header"]] = f"t={ts},v1={sig}"
    else:
        headers[descriptor["sig_header"]] = \
            (descriptor.get("sig_prefix") or "") + sig
        if descriptor.get("ts_header"):
            headers[descriptor["ts_header"]] = str(ts)
    if descriptor.get("id_header"):
        headers[descriptor["id_header"]] = event_id
    return headers


# The reference descriptors from the survey, importable by consumers and
# by the bench; the engine treats them as data like any other.
STANDARD_WEBHOOKS = {
    "family": "timestamp-mac", "sig_format": "prefixed-list",
    "sig_header": "webhook-signature", "sig_prefix": "v1,",
    "ts_header": "webhook-timestamp", "id_header": "webhook-id",
    "canonical": "{id}.{ts}.{body}", "encoding": "base64",
}
STRIPE = {
    "family": "timestamp-mac", "sig_format": "kv-list",
    "sig_header": "stripe-signature", "sig_prefix": "",
    "ts_header": None, "id_header": None,
    "canonical": "{ts}.{body}", "encoding": "hex",
}
GITHUB = {
    "family": "body-mac", "sig_format": "prefixed-list",
    "sig_header": "x-hub-signature-256", "sig_prefix": "sha256=",
    "ts_header": None, "id_header": "x-github-delivery",
    "canonical": "{body}", "encoding": "hex",
}
SLACK = {
    "family": "timestamp-mac", "sig_format": "prefixed-list",
    "sig_header": "x-slack-signature", "sig_prefix": "v0=",
    "ts_header": "x-slack-request-timestamp", "id_header": None,
    "canonical": "v0:{ts}:{body}", "encoding": "hex",
}
