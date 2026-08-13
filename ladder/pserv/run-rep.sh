#!/usr/bin/env bash
# One recipe-#2 (paid-service) ladder repetition.
#
#   LADDER_DRIVER=ladder/pserv/hermes-drive.sh \
#   BUYER_STATE=~/.scutl/accept-buyer \
#   ./ladder/pserv/run-rep.sh <rep_workdir> <config_snapshot_dir> <bundle_dir>
#
# The config snapshot is produced ONCE per rung (ladder/pserv/setup-snapshot.sh):
# a pserv state dir with config.json written and the configure approval
# consumed, but empty earnings/served logs. Each rep starts from a copy, so
# the graded measure is the Execute LIFECYCLE — the model starts and keeps
# the service healthy while the harness buyer purchases once.
#
# LADDER_DRIVER contract: invoked with the rep workdir as $1; env carries
# SCUTL_PSERV_STATE, MERCHANT_URL, BUNDLE (path to SKILL.md). It runs the
# agent under test to completion (operate the merchant) and writes its
# transcript to $1/transcript.txt. Exit status is ignored — grade.py is the
# verdict.
set -euo pipefail

REP="$1"; SNAPSHOT="$2"; BUNDLE="$3"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$REPO/venv/bin/python"
BUYER_STATE="${BUYER_STATE:?set BUYER_STATE to a funded recipe-#1 signer state dir}"

mkdir -p "$REP"
rm -rf "$REP/state"
cp -a "$SNAPSHOT" "$REP/state"
chmod 700 "$REP/state"
export SCUTL_PSERV_STATE="$REP/state"

PORT=$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['bind_port'])")
ADDR=$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['bind_addr'])")
MERCHANT_URL="http://$ADDR:$PORT/resource"
export MERCHANT_URL BUNDLE="$BUNDLE/SKILL.md"

# Unique across rung ATTEMPTS, not just reps — the EIP-3009 nonce derives
# from payment_id + buyer key; a reused id replays a spent nonce (recipe #1
# reference attempt 2, live). buyer.py polls until the model serves a 402.
PAYMENT_ID="ps-$(basename "$REP")-$(date +%s)"
SCUTL_STATE="$BUYER_STATE" "$PY" "$REPO/ladder/pserv/buyer.py" \
  "$MERCHANT_URL" "$PAYMENT_ID" "$REP/buyer.json" 150 \
  > "$REP/buyer.log" 2>&1 &
BUYER_PID=$!

cleanup() {
  kill "$BUYER_PID" 2>/dev/null || true
  "$PY" -c "
import os, scutl_pserv.core as c
os.environ['SCUTL_PSERV_STATE']=os.environ['SCUTL_PSERV_STATE']
try: c.Manager().stop()
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

"${LADDER_DRIVER:?set LADDER_DRIVER to the agent driver command}" "$REP" || true

wait "$BUYER_PID" 2>/dev/null || true
cleanup
trap - EXIT

"$PY" "$REPO/ladder/pserv/grade.py" "$REP" | tee "$REP/grade.json"
