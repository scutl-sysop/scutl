"""State directory: the single source of truth for everything safety-relevant.

Layout (state_dir, default ~/.scutl/odom, dir mode 0700):
  config.json    walls: tld_allowlist, annual_price_ceiling_usd,
                 renewal_horizon_days, balance_floor_usd, max_domains,
                 ns_sets (blessed nameserver sets for od_delegate),
                 auto_renew_backstop — written only by human-approved
                 admin ops                                        (0600)
  api.json       the registrar keypair (apikey + secretapikey)    (0600)
  ledger.log     append-only JSONL: quote / buy-intent / buy-outcome /
                 renew-intent / renew-outcome / delegate / export-report
                 events; holdings and quote lookups derive from it
  approvals/     consumable human-approval token files

One secret (the API keypair), revocable at the registrar's portal.
Unlike sweb there is no second data-plane credential: the registrar is
one plane, and the domain itself is the asset — the ledger holds no
secret, only money facts and identity facts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class NotConfigured(Exception):
    """No config.json yet; run 'odom admin configure' first."""


class NoApiKey(Exception):
    """No api.json yet; run 'odom admin set-key' first."""


class StateDir:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(
            root
            or os.environ.get("SCUTL_ODOM_STATE")
            or Path.home() / ".scutl" / "odom"
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
    def api_creds_file(self) -> Path:
        return self.root / "api.json"

    @property
    def ledger_log(self) -> Path:
        return self.root / "ledger.log"

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

    def load_api_creds(self) -> dict:
        if not self.api_creds_file.exists():
            raise NoApiKey(str(self.api_creds_file))
        creds = json.loads(self.api_creds_file.read_text())
        if not creds.get("apikey") or not creds.get("secretapikey"):
            raise NoApiKey(f"{self.api_creds_file} lacks apikey/secretapikey")
        return creds

    # -- config (integrity-critical: the walls live here) ---------------
    def load_config(self) -> dict:
        if not self.config_file.exists():
            raise NotConfigured(str(self.config_file))
        return json.loads(self.config_file.read_text())

    def save_config(self, config: dict) -> None:
        self.write_secret(self.config_file, json.dumps(config, indent=2).encode())

    # -- ledger (append-only; holdings and quotes derive from it) --------
    def append_event(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.ledger_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_events(self) -> list[dict]:
        if not self.ledger_log.exists():
            return []
        return [json.loads(line)
                for line in self.ledger_log.read_text().splitlines() if line]

    def quote(self, quote_id: str) -> dict | None:
        """Newest quote event with this id (quotes are immutable; a
        re-quote is a NEW id — the pin belongs to one decision)."""
        for e in reversed(self.read_events()):
            if e.get("event") == "quote" and e.get("quote_id") == quote_id:
                return e
        return None

    def holdings(self) -> list[str]:
        """Domains this tool bought (buy-outcome ok) — the log's view;
        reconcile joins it against the registrar's."""
        held: list[str] = []
        for e in self.read_events():
            if e.get("event") == "buy-outcome" and e.get("ok"):
                if e["domain"] not in held:
                    held.append(e["domain"])
        return held

    def unresolved_intents(self) -> list[dict]:
        """buy/renew intents with no outcome — crash evidence. The intent
        carries the idempotency key, so the crashed op is replayable."""
        outcomes = {(e["event"].split("-")[0], e["intent_id"])
                    for e in self.read_events()
                    if e.get("event") in ("buy-outcome", "renew-outcome")}
        out = []
        for e in self.read_events():
            if e.get("event") in ("buy-intent", "renew-intent"):
                op = e["event"].split("-")[0]
                if (op, e["intent_id"]) not in outcomes:
                    out.append({"op": op, "intent_id": e["intent_id"],
                                "domain": e["domain"],
                                "idem_key": e["idem_key"]})
        return out

    def last_balance_snapshot(self) -> int | None:
        """Balance (cents) after the newest logged billable outcome."""
        for e in reversed(self.read_events()):
            if e.get("event") in ("buy-outcome", "renew-outcome") \
                    and e.get("balance_after") is not None:
                return e["balance_after"]
        return None
