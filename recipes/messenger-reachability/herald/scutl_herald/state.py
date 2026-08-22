"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/herald, dir mode 0700):
  config.json           owner_peer_id + all ceilings — written only by
                        human-approved admin ops                      (0600)
  channel.cred          the channel credential; the ONLY secret here.
                        Placed by the HUMAN (actor: human in the manifest);
                        the tool reads it for provider auth and never
                        echoes it anywhere                            (0600)
  herald.log            append-only JSONL: every send (keyed by the
                        caller's send_key, appended BEFORE the provider
                        call) and every inbound read. Cap accounting and
                        seen-state derive from it — not from memory
  batch.json            the current inbound fetch batch (headers only).
                        The fetch cap is enforced as batch membership
  decommission.marker   written by decommission(); send/fetch/read
                        refuse thereafter — status does NOT (reading the
                        record is never gated)
  approvals/            consumable human-approval token files
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Decommissioned(Exception):
    """Marker present; send/fetch/read refuse. status does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'herald admin configure' first."""


class NoCredential(Exception):
    """No channel.cred yet; the human places it (setup step 'credential')."""


class DuplicateKey(Exception):
    """This send_key is already in herald.log. Exit 6: the message
    already went (or was in flight when a crash hit) — dedup working;
    never re-send under a new key."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_HERALD_STATE")
            or Path.home() / ".scutl" / "herald"
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
    def credential_file(self) -> Path:
        return self.root / "channel.cred"

    @property
    def herald_log(self) -> Path:
        return self.root / "herald.log"

    @property
    def batch_file(self) -> Path:
        return self.root / "batch.json"

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

    # -- credential (read for provider auth; never echoed) --------------
    def load_credential(self) -> str:
        if not self.credential_file.exists():
            raise NoCredential(str(self.credential_file))
        return self.credential_file.read_text().strip()

    # -- config (integrity-critical: the owner id and every cap) ---------
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

    # -- herald log (append-only; caps and seen-state derive from it) ----
    def append_record(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.herald_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_records(self) -> list[dict]:
        if not self.herald_log.exists():
            return []
        return [json.loads(l) for l in self.herald_log.read_text().splitlines() if l]

    def send_records(self) -> list[dict]:
        return [r for r in self.read_records() if r["kind"] == "send"]

    def sent_keys(self) -> set[str]:
        return {r["key"] for r in self.send_records()}

    def read_ids(self) -> set[str]:
        return {r["id"] for r in self.read_records() if r["kind"] == "read"}

    # -- inbound batch (fetch-cap enforcement surface; headers only) -----
    def save_batch(self, headers: list[dict], remaining: int) -> None:
        fd = os.open(self.batch_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(
                {"messages": headers, "remaining": remaining}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def load_batch(self) -> dict:
        if not self.batch_file.exists():
            return {"messages": [], "remaining": 0}
        return json.loads(self.batch_file.read_text())

    def batch_header(self, message_id: str) -> dict | None:
        for h in self.load_batch()["messages"]:
            if h["id"] == message_id:
                return h
        return None
