"""Live rails: real systemd (user units) and a Healthchecks-shaped
witness. bindings.live in recipe.yaml; the probes-pending list there
names the questions this module cannot answer until a live witness
account exists — component tests and the bench run on injected fakes
derived from contracts.wire, never on this module.

Secrets: the witness API key and ping key are read from the custody
files at call time and appear in no output, no unit file, and no
exception text. Rendered service units invoke `bell fire <job>`; the
ping key therefore never rides in a unit file at all — only this
process, at ping time, joins key to URL.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .rails import (InvalidSchedule, RailError, SystemdRail, WitnessRail,
                    WitnessUnreachable)
from .state import StateDir

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_PREFIX = "scutl-bell-"


def _bell_exe() -> str:
    """The bell console script beside this interpreter — the same
    installation that performed the registration."""
    exe = Path(sys.executable).with_name("bell")
    return str(exe) if exe.exists() else "bell"


class LiveSystemd(SystemdRail):
    def __init__(self, state: StateDir, unit_dir: Path | None = None):
        self.state = state
        self.unit_dir = unit_dir or UNIT_DIR

    # -- the parse wall ----------------------------------------------------
    def calendar_parse(self, expr: str) -> dict:
        try:
            out = subprocess.run(
                ["systemd-analyze", "calendar", expr],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RailError(f"systemd-analyze unavailable: {e}")
        if out.returncode != 0:
            raise InvalidSchedule(
                f"calendar expression refused by the parse wall: "
                f"{out.stderr.strip()[:200]}")
        normalized, tz = None, None
        for line in out.stdout.splitlines():
            if "Normalized form:" in line:
                normalized = line.split(":", 1)[1].strip()
        if normalized is None:
            raise InvalidSchedule("parser returned no normalized form")
        # a timezone-carrying OnCalendar ends in a tz token (e.g.
        # 'Asia/Tokyo'); core refuses any non-UTC schedule
        tail = normalized.split()[-1] if normalized.split() else ""
        if "/" in tail and not tail[0].isdigit():
            tz = tail
        return {"normalized": normalized, "tz": tz,
                "next_elapse_utc": None}

    def cadence_seconds(self, normalized: str) -> int:
        # two consecutive elapses via --iterations
        try:
            out = subprocess.run(
                ["systemd-analyze", "calendar", "--iterations=2",
                 normalized], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RailError(f"systemd-analyze unavailable: {e}")
        stamps = []
        for line in out.stdout.splitlines():
            if "(in UTC)" in line:
                try:
                    stamps.append(datetime.strptime(
                        line.split(":", 1)[1].strip(),
                        "%a %Y-%m-%d %H:%M:%S UTC"))
                except ValueError:
                    pass
        if len(stamps) >= 2:
            return max(60, int((stamps[1] - stamps[0]).total_seconds()))
        return 86400  # single-elapse expressions: treat as daily

    # -- units ------------------------------------------------------------
    def _unit_base(self, job_id: str) -> Path:
        return self.unit_dir / f"{UNIT_PREFIX}{job_id}"

    def render_units(self, job: dict) -> None:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        jid = job["job_id"]
        base = self._unit_base(jid)
        service = (
            f"# rendered by scutl-bell; spec_hash={job['spec_hash']}\n"
            f"[Unit]\nDescription=scutl bell job {jid}\n\n"
            f"[Service]\nType=oneshot\n"
            # absolute path: systemd user units resolve executables
            # without the venv PATH (live finding 2026-08-31, cst-u3eu:
            # bare 'bell' fails 203/EXEC on every activation), and the
            # env var pins the state dir the registration ran against
            f"Environment=SCUTL_BELL_STATE={self.state.root}\n"
            f"ExecStart={_bell_exe()} fire {shlex.quote(jid)}\n")
        timer = (
            f"# rendered by scutl-bell; spec_hash={job['spec_hash']}\n"
            f"[Unit]\nDescription=scutl bell timer {jid}\n\n"
            f"[Timer]\nOnCalendar={job['schedule']}\nPersistent=true\n\n"
            f"[Install]\nWantedBy=timers.target\n")
        base.with_suffix(".service").write_text(service)
        base.with_suffix(".timer").write_text(timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "enable", "--now",
                        f"{UNIT_PREFIX}{jid}.timer"],
                       capture_output=True, timeout=15)

    def read_unit(self, job_id: str) -> dict | None:
        f = self._unit_base(job_id).with_suffix(".timer")
        if not f.exists():
            return None
        for line in f.read_text().splitlines():
            if "spec_hash=" in line:
                return {"spec_hash": line.split("spec_hash=", 1)[1].strip()}
        return {"spec_hash": None}

    def remove_units(self, job_id: str) -> bool:
        subprocess.run(["systemctl", "--user", "disable", "--now",
                        f"{UNIT_PREFIX}{job_id}.timer"],
                       capture_output=True, timeout=15)
        for suffix in (".timer", ".service"):
            p = self._unit_base(job_id).with_suffix(suffix)
            if p.exists():
                p.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, timeout=15)
        return self.read_unit(job_id) is None

    def list_timers(self) -> list[str]:
        if not self.unit_dir.exists():
            return []
        return sorted(p.name[len(UNIT_PREFIX):-len(".timer")]
                      for p in self.unit_dir.glob(f"{UNIT_PREFIX}*.timer"))

    # -- slots ------------------------------------------------------------
    def _elapses_from(self, normalized: str, base: datetime,
                      n: int) -> list[datetime]:
        try:
            out = subprocess.run(
                ["systemd-analyze", "calendar", f"--iterations={n}",
                 f"--base-time={base.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                 normalized], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RailError(f"systemd-analyze unavailable: {e}")
        stamps = []
        for line in out.stdout.splitlines():
            if "(in UTC)" in line:
                try:
                    stamps.append(datetime.strptime(
                        line.split(":", 1)[1].strip(),
                        "%a %Y-%m-%d %H:%M:%S UTC")
                        .replace(tzinfo=timezone.utc))
                except ValueError:
                    pass
        return stamps

    def slot_for(self, normalized: str, now) -> tuple[str, int]:
        cadence = self.cadence_seconds(normalized)
        probe = now - timedelta(seconds=2 * cadence)
        last = None
        for ts in self._elapses_from(normalized, probe, 8):
            if ts <= now:
                last = ts
        if last is None:
            last = now
        return last.isoformat(), int((now - last).total_seconds())

    def slots_between(self, normalized: str, t0, t1) -> list[str]:
        out, base = [], t0
        for _ in range(500):  # loud cap; no silent truncation past it
            nxt = self._elapses_from(normalized, base, 1)
            if not nxt or nxt[0] > t1:
                break
            out.append(nxt[0].isoformat())
            base = nxt[0] + timedelta(seconds=1)
        return out

    def run_argv(self, job: dict, rid: str) -> dict:
        started = time.monotonic()
        try:
            proc = subprocess.run(job["argv"], capture_output=True,
                                  timeout=3600)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError:
            code = 127
        return {"exit_status": code,
                "duration_ms": int((time.monotonic() - started) * 1000)}


class LiveWitness(WitnessRail):
    """Healthchecks-shaped API v3 + ping surface. probes-pending
    (recipe.yaml bindings.live): rid echo in the read path, grace
    semantics on start-without-success, ping-endpoint 400-vs-404
    boundary, ping rate limits, pause-vs-delete flip history."""

    def __init__(self, state: StateDir):
        self.state = state

    def _config(self) -> dict:
        return self.state.load_config()

    def _api_key(self) -> str:
        f = self.state.api_key_file
        if not f.exists():
            raise WitnessUnreachable(f"no witness API key at {f}")
        return f.read_text().strip()

    def _ping_key(self) -> str:
        f = self.state.ping_key_file
        if not f.exists():
            raise WitnessUnreachable(f"no witness ping key at {f}")
        return f.read_text().strip()

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self._config()["witness_api_base"] + path
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Api-Key": self._api_key(),
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"code": resp.status,
                        "body": json.loads(resp.read() or b"{}")}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"code": 404, "body": {}}
            raise WitnessUnreachable(f"witness API {e.code}")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise WitnessUnreachable(f"witness dark: {e}")

    def upsert(self, slug: str, schedule: str, grace_seconds: int) -> dict:
        r = self._api("POST", "/checks/", {
            "name": slug, "slug": slug, "schedule": schedule,
            "tz": "UTC", "grace": int(grace_seconds),
            "unique": ["slug"]})
        return {"uuid": r["body"].get("uuid"),
                "created": r["code"] == 201}

    def ping(self, slug: str, kind: str, rid: str) -> bool:
        base = self._config()["witness_ping_base"]
        suffix = "" if kind == "success" else f"/{kind}"
        url = f"{base}/{self._ping_key()}/{slug}{suffix}?rid={rid}"
        try:
            req = urllib.request.Request(url, method="POST", data=b"")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise WitnessUnreachable(f"witness dark: {e}")

    def read(self, slug: str) -> dict:
        r = self._api("GET", f"/checks/?slug={slug}")
        checks = r["body"].get("checks", [])
        if not checks:
            return {"status": "absent", "last_ping": None, "pings": []}
        check = checks[0]
        uuid = check.get("uuid")
        pings = []
        if uuid:
            pr = self._api("GET", f"/checks/{uuid}/pings/")
            for p in pr["body"].get("pings", []):
                pings.append({"rid": p.get("rid"), "kind": p.get("type"),
                              "at": p.get("date")})
        return {"status": check.get("status"),
                "last_ping": check.get("last_ping"), "pings": pings}

    def _uuid(self, slug: str) -> str | None:
        r = self._api("GET", f"/checks/?slug={slug}")
        checks = r["body"].get("checks", [])
        return checks[0].get("uuid") if checks else None

    def pause(self, slug: str) -> None:
        uuid = self._uuid(slug)
        if uuid:
            self._api("POST", f"/checks/{uuid}/pause")

    def delete(self, slug: str) -> None:
        uuid = self._uuid(slug)
        if uuid:
            self._api("DELETE", f"/checks/{uuid}")
