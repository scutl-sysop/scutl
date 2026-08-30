"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/bell, dir mode 0700):
  config.json      walls: max_jobs, grace_divisor, grace_cap_minutes,
                   verifier_horizon_factor, unwitnessed_streak_threshold,
                   witness_api_base, witness_ping_base — written only by
                   human-approved admin ops                        (0600)
  jobs/            one JSON per registered job: argv, normalized
                   schedule, cadence, grace, spec hash, witness uuid,
                   optional tombstone; ids starting with '_' are
                   internal (the verifier's own bell) and never count
                   against max_jobs                                (0600)
  firing.log       append-only JSONL: register / fire / fire-witness /
                   zombie-fire / deregister events. THE ONLY SOURCE of
                   the word 'ran' (manifest firing-record leaf); the
                   witness corroborates by rid, never testifies alone
  verify.log       append-only JSONL: one line per reconciliation run —
                   slot accounting and breaches; the verifier's own
                   deafness horizon is measured against this file
  witness_api.key  the project-scoped read-write key             (0600)
  witness_ping.key the project ping key; ping URLs embed it, so units
                   and reports reference the FILE, never the value (0600)
  approvals/       consumable human-approval tokens

No billing credential lives here (second moneyless recipe): the
secrets are the witness keys, and the only thing losing them costs is
corroboration — the firing ledger stays ours.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'bell admin configure' first."""


class UnknownJob(Exception):
    """No such registered job."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_BELL_STATE")
            or Path.home() / ".scutl" / "bell"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "jobs").mkdir(exist_ok=True)
        (self.root / "approvals").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def firing_log(self) -> Path:
        return self.root / "firing.log"

    @property
    def verify_log(self) -> Path:
        return self.root / "verify.log"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    @property
    def api_key_file(self) -> Path:
        return self.root / "witness_api.key"

    @property
    def ping_key_file(self) -> Path:
        return self.root / "witness_ping.key"

    # -- secret handling (secrets never leave this module as output) ----
    def write_secret(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.init()
        self.write_secret(self.config_file,
                          json.dumps(config, indent=2).encode())

    # -- jobs (ids with '_' are internal: the verifier's own bell) -------
    def job_file(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def load_job(self, job_id: str) -> dict:
        f = self.job_file(job_id)
        if not f.exists():
            raise UnknownJob(job_id)
        return json.loads(f.read_text())

    def save_job(self, job_id: str, record: dict) -> None:
        self.init()
        self.write_secret(self.job_file(job_id),
                          json.dumps(record, indent=2).encode())

    def job_ids(self, include_internal: bool = False,
                include_tombstoned: bool = False) -> list[str]:
        if not self.jobs_dir.exists():
            return []
        ids = []
        for p in sorted(self.jobs_dir.glob("*.json")):
            if not include_internal and p.stem.startswith("_"):
                continue
            if not include_tombstoned:
                if json.loads(p.read_text()).get("tombstoned"):
                    continue
            ids.append(p.stem)
        return ids

    # -- the two ledgers (append-only; everything derives from them) -----
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

    def append_firing(self, record: dict) -> None:
        self._append(self.firing_log, record)

    def read_firings(self) -> list[dict]:
        return self._read(self.firing_log)

    def append_verify(self, record: dict) -> None:
        self._append(self.verify_log, record)

    def read_verifies(self) -> list[dict]:
        return self._read(self.verify_log)
