"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/capability, dir mode 0700):
  config.json           limits: plan allowlist, max_purchase_usd — written
                        only by human-approved admin ops              (0600)
  api.key               the vendor-issued API key; the ONLY secret here.
                        Unlike prov's key the human never sees it either:
                        it arrives in the purchase response and goes
                        straight to disk                              (0600)
  usage.log             append-only JSONL: one record per purchase and per
                        successful call; the current plan and the local
                        usage counter always derive from it
  decommission.marker   written by decommission(); purchase and call
                        refuse thereafter — status does NOT (reading the
                        record is never gated)
  approvals/            consumable human-approval token files

The local usage counter is the tool's own ledger. The vendor keeps its
own; status() compares the two and reports disagreement instead of
silently adopting either side.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Decommissioned(Exception):
    """Decommission marker present; purchase and call refuse. status does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'capp admin configure' first."""


class NoApiKey(Exception):
    """No api.key yet; a successful purchase writes it."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_CAPP_STATE")
            or Path.home() / ".scutl" / "capability"
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
    def usage_log(self) -> Path:
        return self.root / "usage.log"

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
    def write_secret(self, data: bytes) -> None:
        fd = os.open(self.api_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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

    # -- usage log (append-only; plan and counter derive from it) --------
    def append_usage_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.usage_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_usage_events(self) -> list[dict]:
        if not self.usage_log.exists():
            return []
        return [json.loads(l) for l in self.usage_log.read_text().splitlines() if l]

    def current_purchase(self) -> dict | None:
        """The latest purchase record, per the log alone."""
        latest = None
        for rec in self.read_usage_events():
            if rec["event"] == "purchased":
                latest = rec
        return latest

    def local_used(self) -> int:
        """Successful calls logged against the current purchase."""
        current = self.current_purchase()
        if current is None:
            return 0
        return sum(1 for rec in self.read_usage_events()
                   if rec["event"] == "call"
                   and rec["purchase_id"] == current["purchase_id"])

    def log_purchase_ids(self) -> set[str]:
        return {rec["purchase_id"] for rec in self.read_usage_events()
                if rec["event"] == "purchased"}
