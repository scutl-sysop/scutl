"""The wire contracts (recipe.yaml contracts.wire), as protocols.

Two counterparties, both injectable so the mocked twin can own them:
  ObjectStore — the bucket: put/get/head/list/delete/probe. The twin
                may ack a put and lose it, return altered or truncated
                bytes, report metadata that contradicts the bytes, show
                drift in either direction, or go unreachable. Nothing
                in core trusts a store answer it can re-derive from
                bytes it hashed itself.
  Rail        — the subscription: create/destroy/exists. The twin may
                leave a destroyed subscription undead (destroy acked,
                keys still working) — teardown must FAIL that, not
                pass it.

Live implementations are in s3live.py; tests and the SMUTbench twin
implement these directly.
"""

from __future__ import annotations

from typing import Protocol


class MissingObject(Exception):
    """GET/HEAD on a key the store does not hold."""


class StoreUnreachable(Exception):
    """The endpoint did not answer. An honest breach, never a stale green."""


class AuthRefused(Exception):
    """The rail answered 401/403: bad token OR a source IP the service
    user's allowlist does not carry (cst-px98.1 owner ruling — access
    is IP-scoped by design). Distinct from StoreUnreachable and from
    not-found so that an auth failure can NEVER read as 'gone': a
    destroy-verify that mistakes 401 for 404 green-washes an undead
    subscription that is still billing."""


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def head(self, key: str) -> dict: ...          # {size, etag?} — may lie
    def list(self) -> dict[str, int]: ...          # key -> reported size
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class Rail(Protocol):
    def create(self, cluster_id: int, tier_id: int, label: str) -> dict: ...
    def destroy(self, subscription_id: str) -> None: ...
    def exists(self, subscription_id: str) -> bool: ...
