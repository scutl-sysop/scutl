"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/sweb, dir mode 0700):
  config.json          walls: monthly_price_ceiling_usd, max_subscriptions,
                       site_bucket, serving leaf, optional site_name —
                       written only by human-approved admin ops       (0600)
  api.key              the management-plane API key                   (0600)
  s3.json              the data-plane keypair; arrives from the provider
                       at provision/rotate and lives ONLY here        (0600)
  subscription.json    id, cluster, s3_hostname, tier, price — no secrets
  publish.log          append-only JSONL: provision/publish-intent/
                       publish-outcome/rotate/destroy events; the live
                       site manifest always derives from it
  approvals/           consumable human-approval token files

Two secrets, both credentials revocable at the provider (the human's
portal for api.key, regenerate-keys for the s3 pair). The s3 pair is
the one the tool itself must rotate — see core.rotate().
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'sweb admin configure' first."""


class NoApiKey(Exception):
    """No api.key yet; run 'sweb admin set-key' first."""


class NotProvisioned(Exception):
    """No subscription yet; run 'sweb provision' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_SWEB_STATE")
            or Path.home() / ".scutl" / "sweb"
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
    def s3_keys_file(self) -> Path:
        return self.root / "s3.json"

    @property
    def subscription_file(self) -> Path:
        return self.root / "subscription.json"

    @property
    def publish_log(self) -> Path:
        return self.root / "publish.log"

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

    def load_api_key(self) -> str:
        if not self.api_key_file.exists():
            raise NoApiKey(str(self.api_key_file))
        return self.api_key_file.read_text().strip()

    def save_s3_keys(self, access: str, secret: str) -> None:
        self.write_secret(
            self.s3_keys_file,
            json.dumps({"access": access, "secret": secret}).encode())

    def load_s3_keys(self) -> dict:
        if not self.s3_keys_file.exists():
            raise NotProvisioned(str(self.s3_keys_file))
        return json.loads(self.s3_keys_file.read_text())

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.write_secret(self.config_file, json.dumps(config, indent=2).encode())

    # -- subscription record (no secrets) -------------------------------
    def load_subscription(self) -> dict:
        if not self.subscription_file.exists():
            raise NotProvisioned(str(self.subscription_file))
        return json.loads(self.subscription_file.read_text())

    def save_subscription(self, record: dict) -> None:
        self.write_secret(self.subscription_file,
                          json.dumps(record, indent=2).encode())

    def clear_subscription(self) -> None:
        for path in (self.subscription_file, self.s3_keys_file):
            if path.exists():
                path.unlink()

    # -- publish log (append-only; the live manifest derives from it) ---
    def append_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.publish_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.publish_log.exists():
            return []
        return [json.loads(line)
                for line in self.publish_log.read_text().splitlines() if line]

    def publish_ids(self) -> set[str]:
        return {e["publish_id"] for e in self.read_events()
                if e.get("event") == "publish-intent"}

    def live_manifest(self) -> dict[str, dict]:
        """key -> {sha256, size, content_type} from the newest intent whose
        files were VERIFIED serving (outcome names them); a later intent
        overrides per key. Crash between intent and outcome leaves the
        intent visible to reconcile, not to the live manifest."""
        manifest: dict[str, dict] = {}
        intents: dict[str, list[dict]] = {}
        for e in self.read_events():
            if e.get("event") == "publish-intent":
                intents[e["publish_id"]] = e["manifest"]
            elif e.get("event") == "publish-outcome":
                served = set(e.get("served", []))
                for f in intents.get(e["publish_id"], []):
                    if f["key"] in served:
                        manifest[f["key"]] = f
        return manifest

    def unresolved_intents(self) -> list[str]:
        """publish_ids with an intent but no outcome — crash evidence."""
        outcomes = {e["publish_id"] for e in self.read_events()
                    if e.get("event") == "publish-outcome"}
        return [pid for pid in sorted(self.publish_ids()) if pid not in outcomes]
