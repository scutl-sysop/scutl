"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/amail, dir mode 0700):
  config.json     inbox identity, the send allowlist, daily ceiling,
                  first-contact policy — written only by admin configure
                  (owner-ratified per the manifest; not an agent tool) (0600)
  provider.cred   the inbox credential; the ONLY secret here. Placed by
                  the HUMAN (or minted by the wallet's zero-amount auth
                  in a live binding); read for provider auth, never
                  echoed anywhere                                      (0600)
  amail.log       append-only JSONL: every send/reply intent (keyed by
                  the caller's send_id, appended BEFORE the provider
                  call), every result, every label-swap journal entry,
                  and every read of an inbound thread. Ceiling
                  accounting, duplicate detection, prior-contact state,
                  and reconciliation all derive from it — never from
                  memory
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'amail admin configure' first."""


class NoCredential(Exception):
    """No provider.cred yet; the human places it (setup: inbox-owned)."""


class DuplicateSendId(Exception):
    """This send_id is already in amail.log. Exit 6: the send already
    went (or was in flight when a crash hit) — dedup working; never
    re-send under a fresh id."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_AMAIL_STATE")
            or Path.home() / ".scutl" / "amail"
        ).expanduser()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # -- paths ---------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def credential_file(self) -> Path:
        return self.root / "provider.cred"

    @property
    def mail_log(self) -> Path:
        return self.root / "amail.log"

    # -- credential (read for provider auth; never echoed) --------------
    def load_credential(self) -> str:
        if not self.credential_file.exists():
            raise NoCredential(str(self.credential_file))
        return self.credential_file.read_text().strip()

    # -- config (integrity-critical: the allowlist and every ceiling) ----
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.init()
        fd = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- mail log (append-only; everything derives from it) --------------
    def append_record(self, record: dict) -> None:
        self.init()
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.mail_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_records(self) -> list[dict]:
        if not self.mail_log.exists():
            return []
        return [json.loads(l) for l in self.mail_log.read_text().splitlines() if l]

    def send_intents(self) -> list[dict]:
        """Every send/reply INTENT (appended before the provider call)."""
        return [r for r in self.read_records() if r["kind"] == "send-intent"]

    def sent_ids(self) -> set[str]:
        return {r["send_id"] for r in self.send_intents()}

    def send_results(self) -> dict[str, dict]:
        """send_id -> latest result record."""
        out: dict[str, dict] = {}
        for r in self.read_records():
            if r["kind"] == "send-result":
                out[r["send_id"]] = r
        return out
