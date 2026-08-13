"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/paid-service, dir mode 0700):
  config.json           payTo, price, offering, bind — written only by
                        human-approved admin ops                       (0600)
  earnings.log          append-only JSONL, one record per SETTLED sale
  served.log            append-only JSONL, one record per served nonce;
                        replay refusal derives from it on restart
  pserv.pid             pidfile while the daemon runs
  decommission.marker   written by decommission(); start refuses thereafter
  approvals/            consumable human-approval token files

Unlike the wallet's state dir there is NO key material here — the merchant
only names a receiving address. Nothing in this directory is secret; the
0600/0700 modes guard integrity (config is where money goes), not secrecy.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


class Decommissioned(Exception):
    """Service has a decommission marker; start (and serving) refuse."""


class NotConfigured(Exception):
    """No config.json yet; run 'pserv admin configure' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_PSERV_STATE")
            or Path.home() / ".scutl" / "paid-service"
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
    def earnings_log(self) -> Path:
        return self.root / "earnings.log"

    @property
    def served_log(self) -> Path:
        return self.root / "served.log"

    @property
    def pidfile(self) -> Path:
        return self.root / "pserv.pid"

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

    # -- config (integrity-critical: payTo lives here) ------------------
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

    # -- append-only logs (totals/replay always derive from them) -------
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
        return [json.loads(l) for l in path.read_text().splitlines() if l]

    def append_earning(self, record: dict) -> None:
        self._append(self.earnings_log, record)

    def read_earnings(self) -> list[dict]:
        return self._read(self.earnings_log)

    def append_served(self, record: dict) -> None:
        self._append(self.served_log, record)

    def served_nonces(self) -> set[str]:
        return {rec["nonce"] for rec in self._read(self.served_log)}

    def earned_since(self, since: datetime | None = None) -> Decimal:
        total = Decimal("0")
        for rec in self.read_earnings():
            if since is not None and datetime.fromisoformat(rec["ts"]) < since:
                continue
            total += Decimal(rec["amount"])
        return total

    def earned_last_24h(self, now: datetime | None = None) -> Decimal:
        now = now or datetime.now(timezone.utc)
        return self.earned_since(now - timedelta(hours=24))
