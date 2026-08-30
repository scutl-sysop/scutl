"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/beacon, dir mode 0700):
  config.json      walls: max_targets, prober_horizon_factor,
                   prober_horizon_floor_minutes, local_freshness_factor,
                   verifier_horizon_factor — plus verify_cadence_seconds
                   (the bell schedule bc_verify rides, mirrored here so
                   the deafness horizon is computable) and prober_api_base
                   (a parameter, never a constant) — written only by
                   human-approved admin ops                        (0600)
  targets/         one JSON per registered target: url, sentinel,
                   prober cadence, local cadence, spec hash, monitor id,
                   optional tombstone                              (0600)
  probe.log        append-only JSONL: register / probe / zombie-probe /
                   deregister events. THE ONLY SOURCE of local truth
                   (manifest up-truth leaf: the inside half of 'up');
                   the prober corroborates by target+time window, never
                   testifies alone — and its label is never evidence
  verify.log       append-only JSONL: one line per reconciliation run —
                   per-target classification and breaches; the
                   verifier's own deafness horizon is measured here
  prober_api.key   the prober main key — account-wide by the vendor's
                   design (named honestly: a wider radius than bell's
                   project-scoped witness), touched only by register /
                   deregister paths                                (0600)
  approvals/       consumable human-approval tokens

Third moneyless recipe: the secret is the prober key, and the only
thing losing it costs is the outside vantage — the probe ledger stays
ours. (The key being account-wide is exactly why it lives at 0600 and
appears in no report.)
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'beacon admin configure' first."""


class UnknownTarget(Exception):
    """No such registered target."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_BEACON_STATE")
            or Path.home() / ".scutl" / "beacon"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "targets").mkdir(exist_ok=True)
        (self.root / "approvals").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def targets_dir(self) -> Path:
        return self.root / "targets"

    @property
    def probe_log(self) -> Path:
        return self.root / "probe.log"

    @property
    def verify_log(self) -> Path:
        return self.root / "verify.log"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    @property
    def api_key_file(self) -> Path:
        return self.root / "prober_api.key"

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

    # -- targets ---------------------------------------------------------
    def target_file(self, target_id: str) -> Path:
        return self.targets_dir / f"{target_id}.json"

    def load_target(self, target_id: str) -> dict:
        f = self.target_file(target_id)
        if not f.exists():
            raise UnknownTarget(target_id)
        return json.loads(f.read_text())

    def save_target(self, target_id: str, record: dict) -> None:
        self.init()
        self.write_secret(self.target_file(target_id),
                          json.dumps(record, indent=2).encode())

    def target_ids(self, include_tombstoned: bool = False) -> list[str]:
        if not self.targets_dir.exists():
            return []
        ids = []
        for p in sorted(self.targets_dir.glob("*.json")):
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

    def append_probe(self, record: dict) -> None:
        self._append(self.probe_log, record)

    def read_probes(self) -> list[dict]:
        return self._read(self.probe_log)

    def append_verify(self, record: dict) -> None:
        self._append(self.verify_log, record)

    def read_verifies(self) -> list[dict]:
        return self._read(self.verify_log)
