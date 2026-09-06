"""The wire contracts (recipe.yaml contracts.wire), as protocols.

Three counterparties, all injectable so the mocked twin can own them:
  Rail      — the provider API (/v2/databases): cluster lifecycle,
              trusted_ips, logical dbs, the app user, the account
              inventory. The twin may report Rebuilding, widen the
              allowlist, show a fork beside the primary, leave a
              deleted cluster still billing, or go dark. GET hands the
              admin password back in the body — nothing downstream may
              echo a rail response unsanitized.
  Database  — the Postgres wire: migrations, the cluster's migrations
              table, dumps, scratch restores, free disk. The twin may
              ack a migration and not record it (phantom), diverge the
              cluster table from the local ledger, produce dumps that
              later fail restore, and run out of disk mid-restore.
  DumpStore — silo's seam: put/get with silo's own digest wall inside.
              The twin may refuse a put, lose a dump, or corrupt it
              SAME-LENGTH (the #9 grading lesson: a length change
              trips the size wall and the cell tests the wrong thing).

Live implementations are in live.py; tests and the scutbench twin
implement these directly.
"""

from __future__ import annotations

from typing import Protocol


class ClusterUnreachable(Exception):
    """The rail or the Postgres wire did not answer. An honest breach,
    never a stale green."""


class RestoreFailed(Exception):
    """pg_restore into scratch failed outright."""


class RestoreWedged(Exception):
    """The restore ran out of disk (or hung) mid-flight — the exact
    outcome the headroom wall exists to prevent."""


class DumpRefused(Exception):
    """silo refused the put (cap park, deny wall, its own read-back
    failure). The dump did NOT happen; nothing enters the manifest."""


class DumpMissing(Exception):
    """silo no longer holds the requested dump key."""


class Rail(Protocol):
    def create_cluster(self, plan: str, region: str, label: str) -> dict: ...
    def get_cluster(self, cluster_id: str) -> dict | None: ...
    def set_trusted_ips(self, cluster_id: str, ips: list[str]) -> None: ...
    def delete_cluster(self, cluster_id: str) -> str: ...   # "gone" | "still-billing"
    def list_clusters(self) -> list[dict]: ...
    def create_db(self, cluster_id: str, name: str) -> None: ...
    def drop_db(self, cluster_id: str, name: str) -> None: ...
    def create_user(self, cluster_id: str, name: str) -> dict: ...


class Database(Protocol):
    def apply_migration(self, db: str, filename: str, sql: str,
                        checksum: str) -> None: ...
    def ledger_head(self, db: str) -> list[dict]: ...   # [{file, checksum}]
    def dump(self, db: str) -> dict: ...
    def restore_scratch(self, dump_bytes: bytes, scratch_db: str) -> dict: ...
    def free_disk_gb(self) -> float: ...


class DumpStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    # returns the key the store actually filed the dump under: silo
    # names its own objects, so the live seam may answer with a
    # different key than the one requested — the dump manifest records
    # the answer, and get() always asks by it
    def get(self, key: str) -> bytes: ...
