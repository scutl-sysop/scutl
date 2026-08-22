"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/pulse, dir mode 0700):
  config.json           checks registry + period, freshness window,
                        probe-round cap — written only by human-approved
                        admin ops                                     (0600)
  pulse.log             append-only JSONL: every probe result, every
                        ledger snapshot, every digest (keyed by period),
                        every anomaly flag raise/clear. The computed
                        digest fields, freshness, gap disclosure, and
                        flag state all derive from it — not from memory
  decommission.marker   written by decommission(); probe/digest refuse
                        thereafter — status does NOT (reading the record
                        is never gated)
  approvals/            consumable human-approval token files
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Decommissioned(Exception):
    """Marker present; probe/digest refuse. status does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'pulse admin configure' first."""


class DuplicatePeriod(Exception):
    """This period's digest is already in pulse.log. Exit 6: the
    heartbeat already went (or was in flight when a crash hit) — dedup
    working; never compose it again."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_PULSE_STATE")
            or Path.home() / ".scutl" / "pulse"
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
    def pulse_log(self) -> Path:
        return self.root / "pulse.log"

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

    # -- config (integrity-critical: the checks and every window) --------
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

    # -- pulse log (append-only; every computed field derives from it) ---
    def append_record(self, record: dict) -> dict:
        record = dict(record)
        record.setdefault("id", f"r{len(self.read_records()) + 1}")
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.pulse_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def read_records(self) -> list[dict]:
        if not self.pulse_log.exists():
            return []
        return [json.loads(l) for l in self.pulse_log.read_text().splitlines() if l]

    def probe_records(self) -> list[dict]:
        return [r for r in self.read_records() if r["kind"] == "probe"]

    def ledger_records(self) -> list[dict]:
        return [r for r in self.read_records() if r["kind"] == "ledger"]

    def digest_records(self) -> list[dict]:
        return [r for r in self.read_records() if r["kind"] == "digest"]

    def digest_periods(self) -> set[str]:
        return {r["period"] for r in self.digest_records()}

    def open_flags(self) -> list[dict]:
        """Latched anomaly flags: raised minus human-cleared, by check."""
        state: dict[str, dict | None] = {}
        for r in self.read_records():
            if r["kind"] == "flag":
                state[r["check"]] = r
            elif r["kind"] == "flag-clear":
                state[r["check"]] = None
        return [f for f in state.values() if f]
