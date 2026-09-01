"""Live implementations of the wire contracts: Ceph RGW behind SigV4,
and the Vultr object-storage rail.

House position on checksums (recipe.yaml bindings.live): the 2026
flexible-checksum extensions are provider-weather — SDK defaults 400
on Ceph-family backends and multipart ETags are not digests of
anything — so this client sends the one header the backend demands
(x-amz-content-sha256, which tentacle requires in v4 signatures) and
STAYS OFF the x-amz-checksum-* family. The integrity wall lives in
core.py as an agent-side re-hash; whatever ETag comes back is recorded
as advisory and never trusted.

Pure stdlib on purpose: a backup tool with a dependency tree is a
backup tool with a supply chain.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .store import AuthRefused, MissingObject, StoreUnreachable

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_v4(method: str, host: str, path: str, query: str, headers: dict,
            payload_hash: str, access: str, secret: str,
            region: str, now: datetime) -> dict:
    """Return the headers for one SigV4-signed S3 request. Deterministic
    given `now` — the tests pin it and assert stability."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    all_headers = {**headers, "host": host, "x-amz-date": amz_date,
                   "x-amz-content-sha256": payload_hash}
    signed = ";".join(sorted(k.lower() for k in all_headers))
    canonical_headers = "".join(
        f"{k}:{all_headers[k].strip()}\n"
        for k in sorted(all_headers, key=str.lower))
    canonical = "\n".join([
        method, urllib.parse.quote(path, safe="/-_.~"), query,
        canonical_headers, signed, payload_hash])
    scope = f"{datestamp}/{region}/s3/aws4_request"
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(
        ("AWS4" + secret).encode(), datestamp), region), "s3"),
        "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    all_headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}")
    return all_headers


class S3Store:
    """Path-style S3 client against one bucket on a Ceph RGW endpoint."""

    def __init__(self, endpoint: str, bucket: str, access: str, secret: str,
                 region: str = "us-east-1", timeout: int = 60, now_fn=None):
        self.endpoint = endpoint
        self.bucket = bucket
        self._access, self._secret = access, secret
        self.region, self.timeout = region, timeout
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def _request(self, method: str, key: str = "", query: str = "",
                 body: bytes = b"") -> tuple[int, dict, bytes]:
        path = f"/{self.bucket}" + (f"/{key}" if key else "")
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        headers = sign_v4(method, self.endpoint, path, query, {},
                          payload_hash, self._access, self._secret,
                          self.region, self._now())
        url = f"https://{self.endpoint}{urllib.parse.quote(path, safe='/-_.~')}"
        if query:
            url += f"?{query}"
        req = urllib.request.Request(url, data=body or None, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # never let an auth refusal masquerade as any other
                # outcome — teardown reads StoreUnreachable on list()
                # as gone-verified, and a blocked source IP must not
                # green-wash that check (cst-px98.1 owner ruling)
                raise AuthRefused(
                    f"{self.endpoint} {method}: HTTP {e.code} — bad "
                    f"S3 keys OR this host's egress IP is not on the "
                    f"service user's allowlist (IP-scoped by owner "
                    f"ruling; compare `curl -s https://api.ipify.org` "
                    f"with the allowlist and ask the owner to add the "
                    f"exact subnet; never widen it from here)") from e
            return e.code, dict(e.headers), e.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise StoreUnreachable(f"{self.endpoint}: {e}") from e

    def _ensure_bucket(self) -> None:
        status, _, body = self._request("PUT")
        if status not in (200, 204, 409):   # 409 BucketAlreadyOwnedByYou
            raise StoreUnreachable(
                f"bucket ensure failed: HTTP {status} {body[:200]!r}")

    def put(self, key: str, data: bytes) -> None:
        status, _, body = self._request("PUT", key, body=data)
        if status == 404 and b"NoSuchBucket" in body:
            self._ensure_bucket()
            status, _, body = self._request("PUT", key, body=data)
        if status not in (200, 204):
            raise StoreUnreachable(
                f"put {key}: HTTP {status} {body[:200]!r}")

    def get(self, key: str) -> bytes:
        status, _, body = self._request("GET", key)
        if status == 404:
            raise MissingObject(key)
        if status != 200:
            raise StoreUnreachable(f"get {key}: HTTP {status}")
        return body

    def head(self, key: str) -> dict:
        status, headers, _ = self._request("HEAD", key)
        if status == 404:
            raise MissingObject(key)
        if status != 200:
            raise StoreUnreachable(f"head {key}: HTTP {status}")
        return {"size": int(headers.get("Content-Length", -1)),
                "etag": headers.get("ETag", "").strip('"')}

    def list(self) -> dict[str, int]:
        out: dict[str, int] = {}
        token = None
        while True:
            query = "list-type=2"
            if token:
                query += "&continuation-token=" + urllib.parse.quote(token)
            status, _, body = self._request("GET", query=query)
            if status == 404:
                return out          # no bucket yet: an empty, honest answer
            if status != 200:
                raise StoreUnreachable(f"list: HTTP {status}")
            ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            root = ET.fromstring(body)
            for c in root.findall("s3:Contents", ns):
                out[c.findtext("s3:Key", "", ns)] = int(
                    c.findtext("s3:Size", "-1", ns))
            token = root.findtext("s3:NextContinuationToken", None, ns)
            if not token:
                return out

    def delete(self, key: str) -> None:
        status, _, _ = self._request("DELETE", key)
        if status not in (200, 204, 404):
            raise StoreUnreachable(f"delete {key}: HTTP {status}")

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
            return True
        except MissingObject:
            return False


class VultrRail:
    """The subscription rail: /v2/object-storage on api.vultr.com, under
    the SECOND, object-storage-scoped key (never the prov key — recon's
    IAM finding is a feature, not a bug)."""

    API = "https://api.vultr.com/v2"

    def __init__(self, api_key: str, timeout: int = 60):
        self._key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None
                 ) -> tuple[int, bytes]:
        import json as _json
        req = urllib.request.Request(
            f"{self.API}{path}",
            data=_json.dumps(payload).encode() if payload else None,
            method=method,
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                # the choke point: no op downstream may interpret an
                # auth refusal — exists() reading 401 as 'gone' would
                # green-wash an undead, still-billing subscription
                raise AuthRefused(
                    f"api.vultr.com {method} {path}: HTTP {e.code} — "
                    f"the token is invalid OR this host's egress IP is "
                    f"not on the service user's allowlist (access is "
                    f"IP-scoped by owner ruling, cst-px98.1; the "
                    f"allowlist is a console/root act — compare this "
                    f"host's egress, e.g. `curl -s https://api.ipify.org`, "
                    f"against the allowlist and ask the owner to add "
                    f"the exact subnet; never widen it from here)"
                ) from e
            return e.code, e.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise StoreUnreachable(f"api.vultr.com: {e}") from e

    def create(self, cluster_id: int, tier_id: int, label: str) -> dict:
        import json as _json
        status, body = self._request("POST", "/object-storage", {
            "cluster_id": cluster_id, "tier_id": tier_id, "label": label})
        if status not in (200, 201, 202):
            raise StoreUnreachable(
                f"subscription create: HTTP {status} {body[:200]!r}")
        sub = _json.loads(body)["object_storage"]
        return {"subscription_id": sub["id"],
                "endpoint": sub["s3_hostname"],
                "access": sub["s3_access_key"],
                "secret": sub["s3_secret_key"]}

    def destroy(self, subscription_id: str) -> None:
        status, body = self._request(
            "DELETE", f"/object-storage/{subscription_id}")
        if status not in (200, 204):
            raise StoreUnreachable(
                f"subscription destroy: HTTP {status} {body[:200]!r}")

    def exists(self, subscription_id: str) -> bool:
        status, _ = self._request(
            "GET", f"/object-storage/{subscription_id}")
        return status == 200
