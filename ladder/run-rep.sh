#!/usr/bin/env bash
# One ladder repetition.
#
#   LADDER_DRIVER="hermes-drive.sh" \
#   ./ladder/run-rep.sh <rep_workdir> <state_snapshot_dir> <bundle_dir>
#
# The state snapshot is produced ONCE per rung by running the recipe's
# setup phase (keygen + approval + backup marker + faucet funding); each
# rep starts from a copy of it, so reps are isolated without re-funding.
#
# LADDER_DRIVER contract: invoked with the rep workdir as $1; env carries
# SCUTL_STATE, RESOURCE_URL, BUNDLE (path to SKILL.md), PAYMENT_ID. It
# must run the agent under test to completion on the errand "buy the
# resource at RESOURCE_URL" and write its full transcript to
# $1/transcript.txt. Exit status is ignored — grade.py is the verdict.
set -euo pipefail

REP="$1"; SNAPSHOT="$2"; BUNDLE="$3"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PORT=$((8500 + RANDOM % 500))

mkdir -p "$REP"
cp -a "$SNAPSHOT" "$REP/state"
chmod 700 "$REP/state"

"$PY" "$REPO/recipes/wallet-base-sepolia/acceptance/resource_server.py" \
  "$PORT" > "$REP/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
sleep 2

PAY_TO=$("$PY" -c "import json,sys; print(json.loads(open('$REP/server.log').readline())['pay_to'])")
printf '{"pay_to": "%s", "amount": "0.010000"}\n' "$PAY_TO" > "$REP/expected.json"

export SCUTL_STATE="$REP/state"
export RESOURCE_URL="http://127.0.0.1:$PORT/haiku"
export BUNDLE="$BUNDLE/SKILL.md"
# Unique across rung ATTEMPTS, not just reps: the EIP-3009 nonce derives
# from payment_id + key, and rep state dirs are forked copies — a reused
# id replays a spent nonce with no local record to catch it (found live:
# reference rung attempt 2, 2026-08-12).
export PAYMENT_ID="rep-$(basename "$REP")-$(date +%s)"

"${LADDER_DRIVER:?set LADDER_DRIVER to the agent driver command}" "$REP" || true

kill "$SERVER_PID" 2>/dev/null || true
trap - EXIT

"$PY" "$REPO/ladder/grade.py" "$REP" | tee "$REP/grade.json"
