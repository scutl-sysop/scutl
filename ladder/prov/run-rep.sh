#!/usr/bin/env bash
# One recipe-#3 (provision) ladder repetition.
#
#   LADDER_DRIVER=ladder/prov/hermes-drive.sh \
#   ./ladder/prov/run-rep.sh <rep_workdir> <config_snapshot_dir> <bundle_dir> [leaf]
#
# The rep talks to a per-rep mock provider (ladder/prov/mock_vultr.py) on a
# private port, pre-seeded with one FOREIGN instance — every rep exercises
# the foreign-instance guard live. The graded measure is the Execute loop:
# create inside the limits, poll to active, report verbatim, destroy.
#
# LADDER_DRIVER contract: invoked with the rep workdir as $1; env carries
# SCUTL_PROV_STATE, SCUTL_PROV_API, BUNDLE (path to SKILL.md), LEAF. It
# runs the agent under test to completion and writes $1/transcript.txt.
# Exit status is ignored — grade.py is the verdict.
set -euo pipefail

REP="$1"; SNAPSHOT="$2"; BUNDLE="$3"; LEAF="${4:-ip-only}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$REPO/venv/bin/python"
MOCK_PORT="${MOCK_PORT:-8471}"

mkdir -p "$REP"
rm -rf "$REP/state" "$REP/mock-state.json" "$REP/mock-state.json.requests.log"
cp -a "$SNAPSHOT" "$REP/state"
chmod 700 "$REP/state"
export SCUTL_PROV_STATE="$REP/state"
export SCUTL_PROV_API="http://127.0.0.1:$MOCK_PORT"
export BUNDLE="$BUNDLE/SKILL.md" LEAF

"$PY" "$REPO/ladder/prov/mock_vultr.py" \
  "$REP/mock-state.json" "$MOCK_PORT" --seed-foreign \
  > "$REP/mock.log" 2>&1 &
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -s -o /dev/null "http://127.0.0.1:$MOCK_PORT/plans" && break
  sleep 0.1
done

# Per-rep wall clock (cst-8ih.10): stamps span the driver only.
REP_STARTED=$(date +%s)
"${LADDER_DRIVER:?set LADDER_DRIVER}" "$REP"
REP_ENDED=$(date +%s)
printf '{"started": %s, "ended": %s, "duration_s": %s}\n' \
  "$REP_STARTED" "$REP_ENDED" "$((REP_ENDED - REP_STARTED))" > "$REP/timing.json"

kill "$MOCK_PID" 2>/dev/null || true
"$PY" "$REPO/ladder/prov/grade.py" "$REP" --leaf "$LEAF" > "$REP/grade.json" || true
