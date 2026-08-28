"""Provider clients — the ONLY module that talks to the provider.

Two planes, two clients:
  MgmtClient  — Vultr API v2 object-storage endpoints (subscription
                lifecycle, tiers, regenerate-keys). Bearer key from
                state, one header, never in URLs/logs/errors.
  DataClient  — S3-compatible data plane (put/list/delete, AWS SigV4)
                plus unauthenticated public GETs, which are the verify
                surface: what a visitor sees, not what we uploaded.

Secrets discipline: management responses that echo the s3 keypair are
stripped HERE — core never sees a secret in a return value; the pair
goes straight to state via the save_s3_keys callback.

Mocks in tests/ implement these same surfaces; the manifest's contracts
block is the source of truth for ops and failure modes.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.request

API_BASE = "https://api.vultr.com/v2"


class TransientError(Exception):
    """Timeouts, 5xx, 429 — state possibly changed; reconcile before retry."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


def _http(req: urllib.request.Request, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429 or e.code >= 500:
            raise TransientError(f"provider {e.code}: {detail}") from None
        raise PermanentError(f"provider {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise TransientError(
            f"provider unreachable: {getattr(e, 'reason', e)}") from None


class MgmtClient:
    def __init__(self, state, base: str | None = None, timeout: int = 30):
        import os
        self.state = state
        self.base = base or os.environ.get("SCUTL_SWEB_API") or API_BASE
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        key = self.state.load_api_key()
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        raw = _http(req, self.timeout)
        return json.loads(raw) if raw else {}

    def clusters(self) -> list[dict]:
        return self._request("GET", "/object-storage/clusters").get("clusters", [])

    def cluster_tiers(self, cluster_id: int) -> list[dict]:
        return self._request(
            "GET", f"/object-storage/clusters/{cluster_id}/tiers").get("tiers", [])

    def create(self, cluster_id: int, tier_id: int, label: str) -> dict:
        """Returns the PUBLIC view; the s3 keypair is delivered to state
        via save_s3_keys and stripped from the return."""
        sub = self._request("POST", "/object-storage", {
            "cluster_id": cluster_id, "tier_id": tier_id, "label": label,
        }).get("object_storage", {})
        return self._deliver_secrets(sub)

    def list(self) -> list[dict]:
        subs = self._request(
            "GET", "/object-storage?per_page=500").get("object_storages", [])
        return [_strip(s) for s in subs]

    def get(self, sub_id: str) -> dict:
        return _strip(self._request(
            "GET", f"/object-storage/{sub_id}").get("object_storage", {}))

    def regenerate_keys(self, sub_id: str) -> dict:
        sub = self._request(
            "POST", f"/object-storage/{sub_id}/regenerate-keys"
        ).get("s3_credentials", {}) or {}
        return self._deliver_secrets({"id": sub_id, **sub})

    def delete(self, sub_id: str) -> None:
        self._request("DELETE", f"/object-storage/{sub_id}")

    def _deliver_secrets(self, sub: dict) -> dict:
        access = sub.get("s3_access_key")
        secret = sub.get("s3_secret_key")
        if access and secret:
            self.state.save_s3_keys(access, secret)
        return _strip(sub)


def _strip(sub: dict) -> dict:
    """Remove the keypair from any provider echo before it can propagate."""
    return {k: v for k, v in sub.items()
            if k not in ("s3_access_key", "s3_secret_key")}


# -- data plane -------------------------------------------------------------

class DataClient:
    """S3-compatible ops against {bucket}.{s3_hostname}, SigV4-signed with
    the pair from state; public_get is unauthenticated on purpose."""

    def __init__(self, state, s3_hostname: str, timeout: int = 30):
        self.state = state
        self.host = s3_hostname
        self.timeout = timeout

    # -- signing (minimal SigV4, virtual-host style, UNSIGNED-PAYLOAD not
    #    used: we sign the content hash so tampering in flight fails) ----
    def _signed(self, method: str, bucket: str, key: str,
                body: bytes = b"", headers: dict | None = None,
                creds: dict | None = None) -> urllib.request.Request:
        creds = creds or self.state.load_s3_keys()
        host = f"{bucket}.{self.host}"
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        scope_date = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        hdrs = {"host": host, "x-amz-date": amz_date,
                "x-amz-content-sha256": payload_hash, **(headers or {})}
        signed_names = ";".join(sorted(h.lower() for h in hdrs))
        canonical = "\n".join([
            method, "/" + key, "",
            "".join(f"{h}:{hdrs[h].strip()}\n" for h in sorted(hdrs, key=str.lower)),
            signed_names, payload_hash])
        scope = f"{scope_date}/us-east-1/s3/aws4_request"
        to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical.encode()).hexdigest()])
        k = f"AWS4{creds['secret']}".encode()
        for part in (scope_date, "us-east-1", "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        auth = (f"AWS4-HMAC-SHA256 Credential={creds['access']}/{scope}, "
                f"SignedHeaders={signed_names}, Signature={sig}")
        req = urllib.request.Request(
            f"https://{host}/{key}", data=body or None, method=method)
        for h, v in hdrs.items():
            if h != "host":
                req.add_header(h, v)
        req.add_header("Authorization", auth)
        return req

    def put(self, bucket: str, key: str, body: bytes, content_type: str,
            public: bool) -> None:
        headers = {"content-type": content_type}
        if public:
            headers["x-amz-acl"] = "public-read"
        _http(self._signed("PUT", bucket, key, body, headers), self.timeout)

    def list(self, bucket: str, creds: dict | None = None) -> list[str]:
        raw = _http(self._signed("GET", bucket, "", creds=creds), self.timeout)
        import re
        return re.findall(r"<Key>([^<]+)</Key>", raw.decode(errors="replace"))

    def delete(self, bucket: str, key: str) -> None:
        _http(self._signed("DELETE", bucket, key), self.timeout)

    def public_url(self, bucket: str, key: str) -> str:
        return f"https://{bucket}.{self.host}/{key}"

    def public_get(self, bucket: str, key: str) -> tuple[bytes, str]:
        """(body, served content-type) as an anonymous visitor sees it.
        Raises PermanentError on 403/404 — the verify surface."""
        req = urllib.request.Request(self.public_url(bucket, key))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"public {e.code}") from None
            raise PermanentError(f"public {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(
                f"public unreachable: {getattr(e, 'reason', e)}") from None
