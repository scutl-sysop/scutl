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

# Remote mode (public-tls rungs): MERCHANT_SSH names the merchant box; the
# rep's live state is REMOTE, reset from a remote snapshot (SNAPSHOT here is
# the remote dir remote-snapshot.sh produced), and pulled back after the
# driver so grade.py runs verbatim on a local copy. MERCHANT_URL must be the
# public resource URL — the merchant's bind is loopback-only behind the
# proxy, so it cannot be derived from config the way the local mode does.
if [ -n "${MERCHANT_SSH:-}" ]; then
  MERCHANT_URL="${MERCHANT_URL:?remote mode: set MERCHANT_URL to the public resource URL}"
  MERCHANT_STATE="${MERCHANT_STATE:-/root/.scutl/paid-service}"
  export MERCHANT_SSH
  ssh -o BatchMode=yes "$MERCHANT_SSH" "
    set -euo pipefail
    pserv stop >/dev/null 2>&1 || true
    rm -rf '$MERCHANT_STATE'
    cp -a '$SNAPSHOT' '$MERCHANT_STATE'
    chmod 700 '$MERCHANT_STATE'"
else
  cp -a "$SNAPSHOT" "$REP/state"
  chmod 700 "$REP/state"
  export SCUTL_PSERV_STATE="$REP/state"
  PORT=$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['bind_port'])")
  ADDR=$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['bind_addr'])")
  MERCHANT_URL="http://$ADDR:$PORT/resource"
fi
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
  if [ -n "${MERCHANT_SSH:-}" ]; then
    ssh -o BatchMode=yes "$MERCHANT_SSH" "pserv stop" >/dev/null 2>&1 || true
    return
  fi
  "$PY" -c "
import os, scutl_pserv.core as c
os.environ['SCUTL_PSERV_STATE']=os.environ['SCUTL_PSERV_STATE']
try: c.Manager().stop()
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

# Per-rep wall clock (cst-3j3): a failed purchase must not hang a rung
# indefinitely — reference rep-12's model polled forever, blamelessly, for
# a sale that never landed. 124 = timeout, 137 = KILL after grace; the
# marker file turns the kill into a graded, machine-readable RED.
REP_TIMEOUT="${REP_TIMEOUT:-900}"
rm -f "$REP/timeout"
timeout --kill-after=30 "$REP_TIMEOUT" \
  "${LADDER_DRIVER:?set LADDER_DRIVER to the agent driver command}" "$REP" || {
  rc=$?
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    echo "driver exceeded REP_TIMEOUT=${REP_TIMEOUT}s (exit $rc)" > "$REP/timeout"
  fi
}

wait "$BUYER_PID" 2>/dev/null || true

if [ -n "${MERCHANT_SSH:-}" ]; then
  # Pull the rep's evidence home; grade.py (restart probe included) is
  # file-level, so the pulled copy grades exactly like local state.
  rsync -a -e "ssh -o BatchMode=yes" \
    "$MERCHANT_SSH:$MERCHANT_STATE/" "$REP/state/"
  chmod 700 "$REP/state"

  # public-tls leaf: off-box probes against the public origin, replaying the
  # buyer's real settled header when the purchase landed. Runs BEFORE cleanup
  # — every probe is answered by the live merchant (all refusals; none writes
  # state). grade.py folds public.json into the verdict.
  case "$MERCHANT_URL" in https://*)
    ORIGIN=$("$PY" -c "from urllib.parse import urlparse; u=urlparse('$MERCHANT_URL'); print(f'{u.scheme}://{u.netloc}')")
    PROBE_ARGS=()
    if "$PY" -c "import json,sys; sys.exit(0 if json.load(open('$REP/buyer.json')).get('x_payment') else 1)" 2>/dev/null; then
      "$PY" -c "import json; open('$REP/x_payment.txt','w').write(json.load(open('$REP/buyer.json'))['x_payment'])"
      PROBE_ARGS+=(--replay-header "$REP/x_payment.txt")
    fi
    "$PY" "$REPO/ladder/pserv/public_probes.py" "$ORIGIN" \
      --payto "$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['payto'])")" \
      --price "$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['price_usdc'])")" \
      --bind-port "$("$PY" -c "import json; print(json.load(open('$REP/state/config.json'))['bind_port'])")" \
      --out "$REP/public.json" "${PROBE_ARGS[@]}" \
      > "$REP/public.log" 2>&1 || true
  ;; esac
fi

cleanup
trap - EXIT

if [ -n "${MERCHANT_SSH:-}" ]; then
  # Re-pull after the stop: cheap incremental sync, and the graded state
  # reflects the merchant at rest (matches local mode, which grades after
  # its own cleanup stop).
  rsync -a -e "ssh -o BatchMode=yes" \
    "$MERCHANT_SSH:$MERCHANT_STATE/" "$REP/state/"
fi

"$PY" "$REPO/ladder/pserv/grade.py" "$REP" | tee "$REP/grade.json"
