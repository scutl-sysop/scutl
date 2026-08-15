"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/provision, dir mode 0700):
  config.json           limits: plan/region allowlists, max_instances,
                        max_hourly_usd, optional dns_subzone — written only
                        by human-approved admin ops                    (0600)
  api.key               the provider API key; the ONLY secret here     (0600)
  instances.log         append-only JSONL: one record per create/destroy;
                        the live set always derives from it
  decommission.marker   written by decommission(); create/dns refuse
                        thereafter — destroy does NOT (recipe invariant:
                        the safe direction is never gated)
  approvals/            consumable human-approval token files

Unlike the wallet there is exactly one secret (api.key) and it is a
credential, not key material: revocation lives in the provider portal,
with the human. The tool must never pretend otherwise.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path


class Decommissioned(Exception):
    """Decommission marker present; create and dns refuse. destroy does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'prov admin configure' first."""


class NoApiKey(Exception):
    """No api.key yet; run 'prov admin set-key' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_PROV_STATE")
            or Path.home() / ".scutl" / "provision"
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
    def api_key_file(self) -> Path:
        return self.root / "api.key"

    @property
    def instances_log(self) -> Path:
        return self.root / "instances.log"

    @property
    def decommission_marker(self) -> Path:
        return self.root / "decommission.marker"

    @property
    def approvals(self) -> Path:
        return self.root / "approvals"

    # -- guards --------------------------------------------------------
    def check_not_decommissioned(self) -> None:
        if self.decommission_marker.exists():
            raise Decommissioned(
                json.loads(self.decommission_marker.read_text())["decommissioned_at"]
            )

    # -- secret handling (api.key never leaves this module as output) ---
    def write_secret(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    def load_api_key(self) -> str:
        if not self.api_key_file.exists():
            raise NoApiKey(str(self.api_key_file))
        return self.api_key_file.read_text().strip()

    # -- config (integrity-critical: the limits live here) --------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        fd = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def max_hourly(self) -> Decimal:
        return Decimal(self.load_config()["max_hourly_usd"])

    # -- instances log (append-only; the live set derives from it) ------
    def append_instance_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.instances_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_instance_events(self) -> list[dict]:
        if not self.instances_log.exists():
            return []
        return [json.loads(l) for l in self.instances_log.read_text().splitlines() if l]

    def log_live_ids(self) -> set[str]:
        """Instance ids created and not yet destroyed, per the log alone."""
        live: set[str] = set()
        for rec in self.read_instance_events():
            if rec["event"] == "created":
                live.add(rec["id"])
            elif rec["event"] == "destroyed":
                live.discard(rec["id"])
        return live
