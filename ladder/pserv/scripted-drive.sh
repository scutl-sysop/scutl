#!/usr/bin/env bash
# LADDER_DRIVER stand-in for the DRY RUN only. Executes exactly the blessed
# commands from the emitted bundle — the "competent agent that follows the
# skill" — so the harness plumbing (rep isolation, buyer timing, grader) can
# be validated GREEN before a GPU is rented. The frontier rung is already
# green by definition; this stands in for it locally. Not used on the pod.
#
# Same env contract as hermes-drive.sh.
set -euo pipefail
REP="$1"
SCUTL_VENV="${SCUTL_VENV:-$HOME/scutl/venv}"
export PATH="$SCUTL_VENV/bin:$PATH"

{
  echo "[scripted] start service"
  pserv start
  pserv status
  echo "[scripted] poll earnings until the customer's sale lands"
  for _ in $(seq 1 60); do
    E=$(pserv earnings)
    COUNT=$(printf '%s' "$E" | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])')
    if [ "$COUNT" -ge 1 ]; then
      TX=$(printf '%s' "$E" | python3 -c 'import json,sys; print(json.load(sys.stdin)["last_settle_tx"])')
      echo "[scripted] sale landed. total earnings: $E"
      echo "[scripted] settle transaction: $TX"
      break
    fi
    sleep 2
  done
} 2>&1 | tee "$REP/transcript.txt"
