"""Substrate client — the ONLY module that talks to sibling components.

Rev 2 (cst-u3eu) blesses two first-party evidence substrates: bell
(scheduled-jobs #11) for the obligations row and beacon
(uptime-monitoring #12) for the services row. Everything crosses this
boundary as the substrate's report dict, verbatim — never reshaped,
only recorded (manifest contracts.substrate).

Three facts about honesty live at this boundary:
  - READ-ONLY BY CONSTRUCTION: the argv comes from a fixed per-kind
    allowlist and is always the report spine. Both substrates' report()
    append nothing (recon §2); running their verify from here would
    append to their verify ledgers and HEAL the deafness the digest
    exists to report. No config field, probe content, or note reaches
    the argv.
  - UNREACHABLE IS ITS OWN TRUTH: a nonzero exit, a timeout, or
    malformed stdout raises SubstrateUnreachable — core renders a red
    `unreachable` row and latches a flag. Stale means "we saw it
    once"; unreachable means "we cannot see it now".
  - THE PAYLOAD IS THE WORLD SPEAKING, TWICE REMOVED: breach lines and
    classification rows quote monitored-world text. Core carries the
    substrate's own labels into the computed table and wraps the
    payload in the untrusted-content envelope on read; nothing above
    this boundary treats any of it as instructions.
"""

from __future__ import annotations

import json
import os
import subprocess

# kind -> (argv, state-dir env var). The allowlist IS the wall: there
# is no code path from config or content to an arbitrary command.
SUBSTRATE_KINDS = {
    "bell": (("bell", "report"), "SCUTL_BELL_STATE"),
    "beacon": (("beacon", "report"), "SCUTL_BEACON_STATE"),
}


class SubstrateUnreachable(Exception):
    """The substrate could not be read NOW — exit nonzero, timeout, or
    stdout that is not a report. Renders red, never absent."""


class SubstrateClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def read(self, kind: str, target: str) -> dict:
        """The substrate's report dict, verbatim. `target` is the
        substrate's state dir, passed via its own env var."""
        argv, state_env = SUBSTRATE_KINDS[kind]
        env = dict(os.environ)
        env[state_env] = target
        try:
            proc = subprocess.run(
                list(argv), env=env, capture_output=True, text=True,
                timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise SubstrateUnreachable(
                f"{kind} report timed out after {self.timeout}s") from None
        except OSError as e:
            raise SubstrateUnreachable(f"{kind} not runnable: {e}") from None
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise SubstrateUnreachable(
                f"{kind} report exited {proc.returncode}: {detail}")
        try:
            report = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            raise SubstrateUnreachable(
                f"{kind} report emitted non-JSON stdout: "
                f"{proc.stdout.strip()[:200]!r}") from None
        if not isinstance(report, dict) or "escalate" not in report:
            raise SubstrateUnreachable(
                f"{kind} report is not a report (no escalate field): "
                f"{str(report)[:200]!r}")
        return report
