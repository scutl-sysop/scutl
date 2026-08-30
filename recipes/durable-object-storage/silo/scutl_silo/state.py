"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/silo, dir mode 0700):
  config.json      walls: storage_cap_gb, monthly_spend_cap_usd,
                   rehearsal_interval_days, rehearsal_horizon_factor,
                   single_put_limit_mb, deny_globs, plus the rail facts
                   (endpoint, bucket, subscription_id, tier prices) —
                   written only by human-approved admin ops       (0600)
  s3.creds         the S3 keypair, JSON {access, secret}; read by the
                   store client, echoed by nothing                (0600)
  manifest.jsonl   append-only JSONL: put / delete / teardown events;
                   the inventory, byte counts, and every report DERIVE
                   from it — there is no second bookkeeping to drift.
                   The LOCAL manifest is authoritative; the copy that
                   rides in the bucket is for disaster symmetry only
  rehearsal.jsonl  append-only JSONL: one line per rehearsal, green and
                   red alike. The word 'restorable' has exactly one
                   source and this file is it

The secrets here unlock the BACKUP rail only: by design (recipe.yaml
decide: key-custody) this state dir never holds the prov key, and the
prov state dir never holds these — a leaked compute credential cannot
delete the backups, and vice versa.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'silo admin configure' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_SILO_STATE")
            or Path.home() / ".scutl" / "silo"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def creds_file(self) -> Path:
        return self.root / "s3.creds"

    @property
    def manifest_log(self) -> Path:
        return self.root / "manifest.jsonl"

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

    def load_creds(self) -> dict:
        if not self.creds_file.exists():
            return {}
        return json.loads(self.creds_file.read_text())

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

    def append_manifest(self, record: dict) -> None:
        self._append(self.manifest_log, record)

    def read_manifest(self) -> list[dict]:
        if not self.manifest_log.exists():
            return []
        return [json.loads(line)
                for line in self.manifest_log.read_text().splitlines() if line]

    def append_rehearsal(self, record: dict) -> None:
        self._append(self.rehearsal_log, record)

    def read_rehearsals(self) -> list[dict]:
        if not self.rehearsal_log.exists():
            return []
        return [json.loads(line)
                for line in self.rehearsal_log.read_text().splitlines() if line]
