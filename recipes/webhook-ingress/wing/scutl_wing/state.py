"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/wing, dir mode 0700):
  config.json    walls: public_base_url, replay_tolerance_seconds,
                 dedup_retention_days, heartbeat_horizon_minutes,
                 reject_spike_threshold, max_senders,
                 rotation_overlap_hours — written only by human-approved
                 admin ops                                        (0600)
  senders/       one JSON per sender: scheme descriptor + secret(s);
                 ids starting with '_' are internal (the heartbeat
                 sender) and never count against max_senders     (0600)
  inbound.log    append-only JSONL: verified / retry / rejected /
                 heartbeat / sender-add / sender-rotate events; the
                 dedup ledger, counters, and every report DERIVE from
                 it — there is no second bookkeeping to drift

Unlike every money recipe there is no billing credential here at all:
the secrets are the per-sender webhook keys, and the only thing they
unlock is the ability to be BELIEVED by this receiver. Losing the
state dir loses the ear, not a wallet.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'wing admin configure' first."""


class UnknownSender(Exception):
    """No such registered sender."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_WING_STATE")
            or Path.home() / ".scutl" / "wing"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "senders").mkdir(exist_ok=True)
        (self.root / "approvals").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def senders_dir(self) -> Path:
        return self.root / "senders"

    @property
    def inbound_log(self) -> Path:
        return self.root / "inbound.log"

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

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.init()
        self.write_secret(self.config_file,
                          json.dumps(config, indent=2).encode())

    # -- senders (descriptor + secrets; ids with '_' are internal) -------
    def sender_file(self, sender_id: str) -> Path:
        return self.senders_dir / f"{sender_id}.json"

    def load_sender(self, sender_id: str) -> dict:
        f = self.sender_file(sender_id)
        if not f.exists():
            raise UnknownSender(sender_id)
        return json.loads(f.read_text())

    def save_sender(self, sender_id: str, record: dict) -> None:
        self.init()
        self.write_secret(self.sender_file(sender_id),
                          json.dumps(record, indent=2).encode())

    def sender_ids(self, include_internal: bool = False) -> list[str]:
        if not self.senders_dir.exists():
            return []
        ids = sorted(p.stem for p in self.senders_dir.glob("*.json"))
        return ids if include_internal else [i for i in ids
                                             if not i.startswith("_")]

    # -- inbound log (append-only; everything derives from it) -----------
    def append_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.inbound_log,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.inbound_log.exists():
            return []
        return [json.loads(line)
                for line in self.inbound_log.read_text().splitlines() if line]
