"""The two failure domains, as injectable rails (contracts.wire).

SystemdRail is the FIRING domain: the calendar parse wall, unit
render/read/remove (read returns the spec hash actually on disk, so
core can three-way-diff ledgered spec vs disk vs witness — tamper
detection lives in that comparison, not in trust), timer inventory,
slot arithmetic, and running the job itself.

WitnessRail is the SECOND domain: check upsert (idempotent by slug),
pings (start / success / fail / exit-status, each carrying the rid
that joins witness log to firing ledger), and the read path verify
reconciles against. Its honest failure mode is WitnessUnreachable —
core records unwitnessed and keeps going; a job never fails because
the witness is down.

The mocked twin (smutbench) and the component tests implement these;
live.py binds them to real systemd and a real Healthchecks-shaped
service. Core never imports live directly.
"""

from __future__ import annotations


class InvalidSchedule(Exception):
    """The calendar parse wall said no (or the schedule is not UTC)."""


class RailError(Exception):
    """The firing rail misbehaved (render failed, systemd unreachable)."""


class WitnessUnreachable(Exception):
    """The second failure domain is dark. Honest, degraded — never fatal
    to a firing; fatal to a registration (an obligation must not begin
    life uncorroboratable)."""


class SystemdRail:
    def calendar_parse(self, expr: str) -> dict:
        """-> {normalized, next_elapse_utc, tz} ; raises InvalidSchedule.
        tz is None for UTC expressions; anything else is refused by core
        (dst-drift is refused at the wall, never accepted-and-skewed)."""
        raise NotImplementedError

    def cadence_seconds(self, normalized: str) -> int:
        """Typical seconds between consecutive elapses."""
        raise NotImplementedError

    def resolvable(self, exe: str) -> bool:
        """Whether a bare argv[0] resolves in this rail's execution
        environment (cst-z3qk). Permissive by default: only a rail
        that actually execs under a constrained PATH (LiveSystemd)
        knows enough to refuse."""
        return True

    def render_units(self, job: dict) -> None:
        """Write timer+service units carrying job['spec_hash']."""
        raise NotImplementedError

    def read_unit(self, job_id: str) -> dict | None:
        """-> {spec_hash} for the unit on disk, or None if absent."""
        raise NotImplementedError

    def remove_units(self, job_id: str) -> bool:
        """-> True when the units are gone; False = still present
        (the zombie shape — core breaches, never shrugs)."""
        raise NotImplementedError

    def list_timers(self) -> list[str]:
        """Job ids with live timers, whether or not the ledger knows
        them (the orphan shape surfaces in the diff)."""
        raise NotImplementedError

    def slot_for(self, normalized: str, now) -> tuple[str, int]:
        """-> (slot_iso, late_seconds): the most recent scheduled slot
        at or before now, and how late this moment is against it."""
        raise NotImplementedError

    def slots_between(self, normalized: str, t0, t1) -> list[str]:
        """ISO slots scheduled in (t0, t1]."""
        raise NotImplementedError

    def run_argv(self, job: dict, rid: str) -> dict:
        """-> {exit_status, duration_ms}. The job itself."""
        raise NotImplementedError


class WitnessRail:
    def upsert(self, slug: str, schedule: str, grace_seconds: int) -> dict:
        """-> {uuid, created: bool}; idempotent by slug (the vendor's
        unique-param upsert). Raises WitnessUnreachable."""
        raise NotImplementedError

    def ping(self, slug: str, kind: str, rid: str) -> bool:
        """kind: start | success | fail | <exit-status>. -> True when
        the ping landed; False (or raise WitnessUnreachable) when the
        second domain is dark — callers record, never crash."""
        raise NotImplementedError

    def read(self, slug: str) -> dict:
        """-> {status, last_ping, pings: [{rid, kind, at}]}.
        Raises WitnessUnreachable. The pings list is CORROBORATION:
        a rid here that the ledger never issued is a custody alarm."""
        raise NotImplementedError

    def pause(self, slug: str) -> None:
        raise NotImplementedError

    def delete(self, slug: str) -> None:
        raise NotImplementedError
