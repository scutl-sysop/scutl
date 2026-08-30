"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/keep, dir mode 0700):
  config.json       walls: monthly_spend_cap_usd, dump_interval_days,
                    rehearsal_interval_days, rehearsal_horizon_factor,
                    scratch_headroom_factor, max_clusters, plus the rail
                    facts (plan, region, plan_rate_usd, expected
                    trusted_ips, migrations_dir, databases, cluster
                    host/port) — written only by human-approved admin
                    ops                                          (0600)
  custody/          admin.creds, app.creds (JSON {user, password}),
                    ca.pem — read by the wire clients, echoed by
                    nothing; the provider hands the admin password back
                    in a plain GET, so custody is where it lives and
                    the ONLY place it lives                (0700/0600)
  cluster.jsonl     append-only: provision / teardown events; the
                    cluster inventory and the orphan wall derive from
                    it
  migrations.jsonl  append-only: one line per applied migration
                    ({ts, db, file, checksum}); the local half of the
                    exactly-once ledger — the cluster's migrations
                    table is the other half, and kp_status diffs them
  dumps.jsonl       append-only: one line per CONFIRMED dump (silo
                    said stored) — ledger head, per-table row counts,
                    per-table digests, dump digest, size estimate
  rehearsal.jsonl   append-only: one line per rehearsal, green, red,
                    parked, and unreachable alike. The word
                    'restorable' has exactly one source and this file
                    is it
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'keep admin configure' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_KEEP_STATE")
            or Path.home() / ".scutl" / "keep"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)
        self.custody.mkdir(exist_ok=True)
        os.chmod(self.custody, 0o700)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def custody(self) -> Path:
        return self.root / "custody"

    @property
    def cluster_log(self) -> Path:
        return self.root / "cluster.jsonl"

    @property
    def migration_log(self) -> Path:
        return self.root / "migrations.jsonl"

    @property
    def dump_log(self) -> Path:
        return self.root / "dumps.jsonl"

    @property
    def rehearsal_log(self) -> Path:
        return self.root / "rehearsal.jsonl"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    # -- secret handling (secrets never leave this module as output) ----
    def write_secret(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    def write_creds(self, name: str, creds: dict) -> None:
        self.write_secret(self.custody / name,
                          json.dumps(creds).encode())

    def load_creds(self, name: str) -> dict:
        p = self.custody / name
        if not p.exists():
            return {}
        return json.loads(p.read_text())

    def secret_values(self) -> list[str]:
        """Every custody secret value, for output scrubbing."""
        out: list[str] = []
        if self.custody.exists():
            for f in self.custody.glob("*.creds"):
                try:
                    out.extend(str(v) for v in json.loads(
                        f.read_text()).values() if v)
                except (json.JSONDecodeError, OSError):
                    pass
        return out

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.init()
        self.write_secret(self.config_file,
                          json.dumps(config, indent=2).encode())

    # -- append-only ledgers (everything derives from them) --------------
    def _append(self, path: Path, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line)
                for line in path.read_text().splitlines() if line]

    def append_cluster(self, record: dict) -> None:
        self._append(self.cluster_log, record)

    def read_cluster(self) -> list[dict]:
        return self._read(self.cluster_log)

    def append_migration(self, record: dict) -> None:
        self._append(self.migration_log, record)

    def read_migrations(self) -> list[dict]:
        return self._read(self.migration_log)

    def append_dump(self, record: dict) -> None:
        self._append(self.dump_log, record)

    def read_dumps(self) -> list[dict]:
        return self._read(self.dump_log)

    def append_rehearsal(self, record: dict) -> None:
        self._append(self.rehearsal_log, record)

    def read_rehearsals(self) -> list[dict]:
        return self._read(self.rehearsal_log)
