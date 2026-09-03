"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/gpu-rental, dir mode 0700):
  config.json          walls: gpu_type allowlist, max_hourly_usd,
                       max_pods, region_pin, volume (id, named cost) —
                       written only by human-approved admin ops   (0600)
  api.key              the RunPod API key; the ONLY secret here   (0600)
  rentals.log          append-only JSONL: created / destroy-requested /
                       destroy-verified events. The live set and every
                       counter DERIVE from it — nothing to drift, and
                       an open rental (created without destroy-verified)
                       is the alarm that keeps ringing
  decommission.marker  create refuses thereafter; destroy does NOT
                       (recipe invariant: the safe direction is never
                       gated)
  approvals/           consumable human-approval token files

Same custody note as prov's: exactly one secret, and it is a
credential, not key material — revocation lives in the RunPod console,
with the human.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path


class Decommissioned(Exception):
    """Marker present; create refuses. destroy does not."""


class NotConfigured(Exception):
    """No config.json yet; run 'gpod admin configure' first."""


class NoApiKey(Exception):
    """No api.key yet; run 'gpod admin set-key' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_GPOD_STATE")
            or Path.home() / ".scutl" / "gpu-rental"
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
    def rentals_log(self) -> Path:
        return self.root / "rentals.log"

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
                json.loads(
                    self.decommission_marker.read_text())["decommissioned_at"])

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

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        fd = os.open(self.config_file,
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def max_hourly(self) -> Decimal:
        return Decimal(self.load_config()["max_hourly_usd"])

    # -- rentals log (append-only; everything derives from it) ----------
    def append_rental_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.rentals_log,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_rental_events(self) -> list[dict]:
        if not self.rentals_log.exists():
            return []
        return [json.loads(l)
                for l in self.rentals_log.read_text().splitlines() if l]

    def open_rentals(self) -> dict[str, dict]:
        """Pod id -> its 'created' record, for rentals not yet closed by
        destroy-verified. destroy-requested does NOT close a rental —
        only verified-gone does (manifest: teardown-proof)."""
        open_: dict[str, dict] = {}
        for rec in self.read_rental_events():
            if rec["event"] == "created":
                open_[rec["id"]] = rec
            elif rec["event"] == "destroy-verified":
                open_.pop(rec["id"], None)
        return open_
