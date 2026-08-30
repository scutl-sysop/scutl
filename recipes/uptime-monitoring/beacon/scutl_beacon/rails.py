"""The two vantage points, as injectable rails (contracts.wire).

LocalRail is the INSIDE face: fetch the service's own health path from
the host and report transport, sentinel presence, and the freshness
serial's age. It proves the process answers, correctly, HERE — and
makes no reachability claim whatsoever.

ProberRail is the OUTSIDE face: an active witness on another failure
domain. Its read is a BATCH (read_all — the live rail is rate-limited
to 10 req/min, so the verifier reads once, not per-target), and every
monitor row carries the byte the whole design rides on:
last_observed_at. An active prober's cheap failure mode is a stale
"up" — a paused or dead monitor keeps saying its last green word
forever — so core reads evidence timestamps, never state labels
(manifest up-truth leaf). Its honest outage mode is ProberUnreachable:
core classifies prober-deaf and labels coverage degraded; a probe
round never fails because the prober is dark.

The mocked twin (smutbench) and the component tests implement these;
live.py binds them to a real UptimeRobot-shaped service and real HTTP.
Core never imports live directly.
"""

from __future__ import annotations


class TargetInvalid(Exception):
    """The spec wall said no (bad URL, missing/weak sentinel, bad cadence)."""


class RailError(Exception):
    """A rail misbehaved in a way that is neither refusal nor outage."""


class ProberUnreachable(Exception):
    """The outside failure domain is dark. Honest, degraded — never fatal
    to a local probe; fatal to a registration (an obligation to watch
    must not begin life unwatchable)."""


class LocalRail:
    def fetch(self, target: dict) -> dict:
        """-> {status_code, sentinel_present, serial_age_seconds} for the
        target's health path, fetched from the host. serial_age_seconds
        is None when the service serves no readable serial; a corpse
        behind a happy proxy shows status 200 with sentinel_present
        False (the 200-from-the-grave shape). Connection failure or
        hang returns {status_code: None, ...} — a failed observation is
        still an observation, recorded honestly."""
        raise NotImplementedError


class ProberRail:
    def upsert(self, name: str, url: str, keyword: str,
               cadence_seconds: int) -> dict:
        """-> {monitor_id, created: bool}; idempotent by name. Raises
        ProberUnreachable. NOTE: core calls this only after checking
        for drift — a silent re-upsert over a drifted monitor launders
        tamper evidence (manifest: watching-the-wrong-door)."""
        raise NotImplementedError

    def read_all(self) -> list[dict]:
        """-> [{monitor_id, name, config: {url, keyword,
        cadence_seconds}, state ('up'|'down'), last_observed_at (ISO),
        paused (bool), incidents: [{from, to, kind}]}] — every monitor
        in the account, whether or not the ledger knows it (foreign
        monitors surface in the diff). ONE call: the live rail is
        rate-limited. Raises ProberUnreachable."""
        raise NotImplementedError

    def pause(self, monitor_id: str) -> None:
        """Approval-gated upstream: pausing a watch silences an alarm."""
        raise NotImplementedError

    def delete(self, monitor_id: str) -> None:
        raise NotImplementedError
