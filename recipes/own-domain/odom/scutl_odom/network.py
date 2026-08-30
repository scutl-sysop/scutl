"""Registrar client — the ONLY module that talks to the registrar.

Porkbun API v3 shape (recon: docs/own-domain-recon.md, spec v3.17):
one JSON-over-POST plane; credentials are injected here from state and
never appear in return values, URLs, logs, or errors. The rail's own
safety features are used, not reimplemented: dryRun for rehearsal,
pinned `cost` (pennies) on create/renew, Idempotency-Key with 24h
replay. Money is integer cents everywhere in this package.

Mocks in tests/ implement this same surface; the manifest's contracts
block is the source of truth for ops and failure modes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API_BASE = "https://api.porkbun.com/api/json/v3"


class TransientError(Exception):
    """Timeouts, 5xx, 429, retryable next_action — state possibly
    changed; reconcile (or replay the same idempotency key) before retry."""


class PermanentError(Exception):
    """Registrar said no and retrying unchanged cannot succeed."""


class PriceMismatch(PermanentError):
    """The pinned cost no longer equals the live price — the rail failed
    closed exactly as designed. Exit 6; a re-quote is a NEW decision."""


class InsufficientFunds(PermanentError):
    """Balance cannot cover the pinned cost. An escalation, never a
    retry loop — the fix (top-up) is the owner's, not the agent's."""


def _classify(message: str, retryable: bool | None) -> Exception:
    low = message.lower()
    if "cost" in low and ("match" in low or "mismatch" in low or
                          "changed" in low or "equal" in low):
        return PriceMismatch(message)
    if "insufficient" in low or "balance" in low and "low" in low:
        return InsufficientFunds(message)
    if retryable:
        return TransientError(message)
    return PermanentError(message)


class RegistrarClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        self.state = state
        self.base = base or os.environ.get("SCUTL_ODOM_API") or API_BASE
        self.timeout = timeout

    def _post(self, path: str, body: dict | None = None,
              idem_key: str | None = None) -> dict:
        creds = self.state.load_api_creds()
        payload = {"apikey": creds["apikey"],
                   "secretapikey": creds["secretapikey"], **(body or {})}
        headers = {"Content-Type": "application/json"}
        if idem_key:
            headers["Idempotency-Key"] = idem_key
        req = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode(),
            method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            try:
                err = json.loads(detail)
                raise _classify(
                    err.get("message", detail),
                    (err.get("next_action") or {}).get("retryable"),
                ) from None
            except (ValueError, KeyError):
                pass
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"registrar {e.code}: {detail}") from None
            raise PermanentError(f"registrar {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"registrar unreachable: {getattr(e, 'reason', e)}") from None
        if out.get("status") == "ERROR":
            raise _classify(out.get("message", "registrar error"),
                            (out.get("next_action") or {}).get("retryable"))
        return _strip(out)

    # -- pricing / availability (no side effects) -----------------------
    def check(self, domain: str) -> dict:
        """{avail, price, firstYearPromo, regularPrice, premium,
        minDuration, additional:{renewal:{...}, transfer:{...}}}"""
        return self._post(f"/domain/checkDomain/{domain}").get("response", {})

    def requirements(self, tld: str) -> dict:
        """{api_registerable: bool, ...schema...}"""
        return self._post(f"/domain/getRegistrationRequirements/{tld}")

    # -- billable (pinned cost + idempotency; dryRun rehearses) ----------
    def create(self, domain: str, cost_cents: int, idem_key: str,
               whois_privacy: bool = True, dry_run: bool = False) -> dict:
        return self._post(f"/domain/create/{domain}", {
            "cost": cost_cents, "agreeToTerms": "yes",
            "whoisPrivacy": whois_privacy,
            **({"dryRun": True} if dry_run else {}),
        }, idem_key=None if dry_run else idem_key)

    def renew(self, domain: str, cost_cents: int, idem_key: str,
              dry_run: bool = False) -> dict:
        return self._post(f"/domain/renew/{domain}", {
            "cost": cost_cents,
            **({"dryRun": True} if dry_run else {}),
        }, idem_key=None if dry_run else idem_key)

    # -- holdings facts (live, never cached) -----------------------------
    def get(self, domain: str) -> dict:
        """{status, createDate, expireDate, securityLock, whoisPrivacy,
        autoRenew} — 0/1 ints for the flags, per the rail."""
        return self._post(f"/domain/get/{domain}").get("domain", {})

    def list_all(self) -> list[str]:
        return [d.get("domain") for d in
                self._post("/domain/listAll").get("domains", [])]

    def set_auto_renew(self, domain: str, on: bool) -> dict:
        return self._post(f"/domain/updateAutoRenew/{domain}",
                          {"autoRenew": "yes" if on else "no"})

    def update_ns(self, domain: str, ns_list: list[str],
                  dry_run: bool = False) -> dict:
        return self._post(f"/domain/updateNs/{domain}", {
            "ns": list(ns_list), **({"dryRun": True} if dry_run else {})})

    # -- account facts ---------------------------------------------------
    def balance(self) -> int:
        return int(self._post("/account/balance").get("balance", 0))

    def api_settings(self) -> dict:
        """{settings:{monthlySpendLimit, autoTopup, ...}, monthlySpend}"""
        return self._post("/account/apiSettings")


def _strip(out: dict) -> dict:
    """Remove any credential echo before it can propagate."""
    return {k: v for k, v in out.items()
            if k not in ("apikey", "secretapikey")}
