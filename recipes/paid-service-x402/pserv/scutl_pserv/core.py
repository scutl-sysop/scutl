"""Merchant core: the guardrail component of recipe #2.

Two halves, both here so the invariants live in one file:

  Merchant  — per-request decision logic: offer, refuse, verify-before-serve.
              Pure enough to test with mocked facilitator (no HTTP, no fork).
  Manager   — daemon lifecycle + admin ops (configure/set-payto/decommission),
              the surface the typed tools expose.

Manifest invariants enforced HERE, in code (recipe.yaml components.pserv):
  - nothing is served before facilitator verify AND settle->txhash
  - payTo comes from config at request time; request content cannot move it
  - a nonce serves at most once, across restarts (served.log is the truth)
  - earnings totals always derive from the append-only earnings.log
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from . import approvals
from .network import (
    NETWORK,
    USDC_ADDRESS,
    FacilitatorClient,
    PermanentError,
    TransientError,
    decode_payment_header,
    encode_payment_response,
    usdc_to_atomic,
)
from .state import StateDir

RESOURCE_PATH = "/resource"
OFFERINGS = ("static-file", "generated-text")

# generated-text offering: deterministic template, varies only by UTC date.
GENERATED_TEMPLATE = """paid in six decimals —
the ledger remembers {date}
better than we do
"""


@dataclass
class Response:
    code: int
    body: bytes
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)

    @classmethod
    def json(cls, code: int, obj: dict, **kw) -> "Response":
        return cls(code, json.dumps(obj).encode(), "application/json", **kw)


class Merchant:
    """Per-request logic. One instance per daemon; state on disk, not here."""

    def __init__(self, state: StateDir, facilitator=None,
                 retry_delays: tuple = (0, 10, 30, 60)):
        self.state = state
        self.facilitator = facilitator or FacilitatorClient()
        self.retry_delays = retry_delays
        self.config = state.load_config()

    # -- offer ---------------------------------------------------------
    def requirements(self) -> dict:
        """payTo and price read from config HERE, at request time — the only
        source; no request input reaches this dict (payto-injection probe)."""
        return {
            "scheme": "exact",
            "network": NETWORK,
            "maxAmountRequired": str(usdc_to_atomic(Decimal(self.config["price_usdc"]))),
            "resource": RESOURCE_PATH,
            "description": self.config.get("description", "scutl paid-service resource"),
            "mimeType": "text/plain",
            "payTo": self.config["payto"],
            "maxTimeoutSeconds": 120,
            "asset": USDC_ADDRESS,
            "extra": {"name": "USDC", "version": "2"},
        }

    def _offer(self, error: str = "payment required") -> Response:
        return Response.json(
            402, {"x402Version": 1, "accepts": [self.requirements()], "error": error}
        )

    # -- content -------------------------------------------------------
    def resource_body(self) -> bytes:
        if self.config["offering"] == "static-file":
            with open(self.config["resource_path"], "rb") as f:
                return f.read()
        date = datetime.now(timezone.utc).date().isoformat()
        return GENERATED_TEMPLATE.format(date=date).encode()

    # -- the serve decision --------------------------------------------
    def handle(self, path: str, payment_header: str | None) -> Response:
        self.state.check_not_decommissioned()
        if path != RESOURCE_PATH:
            return Response.json(404, {"error": "not found"})
        if not payment_header:
            return self._offer()

        try:
            payload = decode_payment_header(payment_header)
            auth = payload["payload"]["authorization"]
            nonce, value = str(auth["nonce"]), int(auth["value"])
        except (ValueError, KeyError, TypeError) as e:
            return Response.json(400, {"error": f"malformed payment: {e}"})

        reqs = self.requirements()
        # In-code refusals BEFORE any facilitator call: an underpayment or a
        # replay must not reach settle at all.
        if value < int(reqs["maxAmountRequired"]):
            return self._offer("underpayment: authorization below price")
        if nonce in self.state.served_nonces():
            return self._offer("replayed authorization: nonce already served")

        for op in (self.facilitator.verify, self.facilitator.settle):
            result, last = None, None
            for delay in self.retry_delays:
                if delay:
                    time.sleep(delay)
                try:
                    result = op(payload, reqs)
                    break
                except TransientError as e:
                    last = e
            else:
                return self._offer(f"{op.__name__} unavailable (transient): {last}")
            if op is self.facilitator.verify:
                continue
            settle = result  # only settle() returns a value

        ts = datetime.now(timezone.utc).isoformat()
        # served.log first: if we crash before responding, the refusal to
        # double-serve survives; a paid-but-unserved request is visible in
        # earnings.log vs served.log reconciliation.
        self.state.append_served({"nonce": nonce, "ts": ts, "tx": settle.tx_hash})
        self.state.append_earning({
            "ts": ts,
            "amount": str(Decimal(value).scaleb(-6)),
            "tx": settle.tx_hash,
            "payer": auth.get("from", ""),
            "nonce": nonce,
        })
        return Response(
            200, self.resource_body(), "text/plain",
            headers={"X-PAYMENT-RESPONSE": encode_payment_response(
                {"success": True, "transaction": settle.tx_hash, "network": settle.network}
            )},
        )

    def handle_safe(self, path: str, payment_header: str | None) -> Response:
        """handle() with permanent facilitator rejections mapped to 402 —
        the daemon must answer every request, never crash on one."""
        try:
            return self.handle(path, payment_header)
        except PermanentError as e:
            return self._offer(str(e))


class Manager:
    """Lifecycle + admin surface behind the typed tools."""

    def __init__(self, state: StateDir | None = None):
        self.state = state or StateDir()

    # -- introspection -------------------------------------------------
    def _pid(self) -> int | None:
        if not self.state.pidfile.exists():
            return None
        pid = int(self.state.pidfile.read_text())
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return None
        return pid

    def status(self) -> dict:
        config = self.state.load_config()
        self.state.check_not_decommissioned()
        pid = self._pid()
        return {
            "running": pid is not None,
            "pid": pid,
            "offering": config["offering"],
            "price_usdc": config["price_usdc"],
            "payto": config["payto"],
            "bind": f"{config['bind_addr']}:{config['bind_port']}",
            "earned_last_24h": str(self.state.earned_last_24h()),
            "sales_total": len(self.state.read_earnings()),
        }

    def earnings(self, since: str | None = None) -> dict:
        self.state.load_config()
        records = self.state.read_earnings()
        if since:
            cutoff = datetime.fromisoformat(since)
            records = [r for r in records if datetime.fromisoformat(r["ts"]) >= cutoff]
        return {
            "count": len(records),
            "total_usdc": str(sum((Decimal(r["amount"]) for r in records), Decimal("0"))),
            "last_settle_tx": records[-1]["tx"] if records else None,
        }

    # -- lifecycle -----------------------------------------------------
    def start(self, wait_secs: float = 5.0) -> dict:
        config = self.state.load_config()
        self.state.check_not_decommissioned()
        if (pid := self._pid()) is not None:
            return {"running": True, "pid": pid, "already_running": True}
        subprocess.Popen(
            [sys.executable, "-m", "scutl_pserv.server"],
            env={**os.environ, "SCUTL_PSERV_STATE": str(self.state.root)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + wait_secs
        while time.monotonic() < deadline:
            if (pid := self._pid()) is not None:
                return {"running": True, "pid": pid,
                        "bind": f"{config['bind_addr']}:{config['bind_port']}"}
            time.sleep(0.1)
        raise RuntimeError("daemon did not come up (no live pidfile)")

    def stop(self) -> dict:
        self.state.load_config()
        pid = self._pid()
        if pid is None:
            return {"running": False, "was_running": False}
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if self._pid() is None:
                break
            time.sleep(0.1)
        self.state.pidfile.unlink(missing_ok=True)
        return {"running": False, "was_running": True}

    # -- admin (human-approved) ----------------------------------------
    def configure(self, payto: str, price_usdc: Decimal, offering: str,
                  bind_addr: str = "127.0.0.1", bind_port: int = 8402,
                  resource_path: str | None = None) -> dict:
        approvals.consume(self.state, "configure")
        if offering not in OFFERINGS:
            raise ValueError(f"unknown offering '{offering}' (valid: {OFFERINGS})")
        if offering == "static-file" and not resource_path:
            raise ValueError("offering static-file requires --resource-path")
        self.state.init()
        config = {
            "payto": payto,
            "price_usdc": str(price_usdc),
            "offering": offering,
            "bind_addr": bind_addr,
            "bind_port": bind_port,
        }
        if resource_path:
            config["resource_path"] = resource_path
        self.state.save_config(config)
        return {"configured": True, **config}

    def set_payto(self, payto: str) -> dict:
        approvals.consume(self.state, "set-payto")
        config = self.state.load_config()
        old = config["payto"]
        config["payto"] = payto
        self.state.save_config(config)
        return {"payto": payto, "previous": old}

    def decommission(self) -> dict:
        approvals.consume(self.state, "decommission")
        self.state.load_config()
        marker = {"decommissioned_at": datetime.now(timezone.utc).isoformat()}
        stopped = self.stop()
        # Marker after stop: stop() itself must not refuse. Config and logs
        # are retained for reconciliation — nothing here is secret.
        self.state.decommission_marker.write_text(json.dumps(marker))
        return {**marker, "was_running": stopped["was_running"]}
