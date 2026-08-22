"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/triage, dir mode 0700):
  config.json           categories + max_fetch_per_run — written only by
                        human-approved admin ops                      (0600)
  mailbox.cred          the mailbox credential; the ONLY secret here.
                        Placed by the HUMAN (actor: human in the manifest);
                        the tool reads it for provider auth and never
                        echoes it anywhere                            (0600)
  triage.log            append-only JSONL: one verdict per message, ever.
                        Seen-state derives from it — not from the provider
                        and not from the model's memory
  batch.json            the current fetch batch (headers only, no bodies).
                        The cap is enforced as batch membership: read /
                        triage / draft refuse ids outside it
  drafts/               one file per reply draft; the human sends (or not)
                        from their own client
  decommission.marker   written by decommission(); fetch/read/triage/draft
                        refuse thereafter — status does NOT (reading the
                        record is never gated)
  approvals/            consumable human-approval token files
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Decommissioned(Exception):
    """Marker present; fetch/read/triage/draft refuse. status does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'sift admin configure' first."""


class NoCredential(Exception):
    """No mailbox.cred yet; the human places it (setup step 'credential')."""


class AlreadyTriaged(Exception):
    """This message id already has a verdict in triage.log. Exit 6:
    idempotency working — skip and move on, never re-file."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_SIFT_STATE")
            or Path.home() / ".scutl" / "triage"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / "approvals").mkdir(exist_ok=True)
        (self.root / "drafts").mkdir(exist_ok=True)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def credential_file(self) -> Path:
        return self.root / "mailbox.cred"

    @property
    def triage_log(self) -> Path:
        return self.root / "triage.log"

    @property
    def batch_file(self) -> Path:
        return self.root / "batch.json"

    @property
    def drafts(self) -> Path:
        return self.root / "drafts"

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

    # -- config (integrity-critical: the cap and categories live here) ---
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

    # -- triage log (append-only; seen-state derives from it) ------------
    def append_verdict(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.triage_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_verdicts(self) -> list[dict]:
        if not self.triage_log.exists():
            return []
        return [json.loads(l) for l in self.triage_log.read_text().splitlines() if l]

    def triaged_ids(self) -> set[str]:
        return {rec["id"] for rec in self.read_verdicts()}

    # -- fetch batch (cap enforcement surface; headers only, no bodies) --
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
