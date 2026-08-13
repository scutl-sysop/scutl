#!/usr/bin/env bash
# Recipe #2 (paid-service) reference rung: N reps per offering leaf,
# sequential. The manifest requires BOTH blessed leaves (static-file AND
# generated-text) to appear in a green rung, so this drives each leaf.
#
#   BUYER_STATE=~/.scutl/accept-buyer \
#   LADDER_DRIVER=ladder/pserv/hermes-drive.sh \
#   RUNG_DIR=~/rung-ref-ps \
#   REPS=15 \
#   ./ladder/pserv/run-rung.sh
#
# Uses the standard bundle by default (reference rung); set BUNDLE_PROFILE=smol
# for the headline rung.
set -u
cd "$(dirname "$0")/../.."
REPO="$PWD"

REPS="${REPS:-15}"
RUNG_DIR="${RUNG_DIR:-$HOME/rung-ref-ps}"
BUYER_STATE="${BUYER_STATE:?set BUYER_STATE}"
PROFILE="${BUNDLE_PROFILE:-standard}"
PAYTO="${PAYTO:?set PAYTO (address-only receiving wallet)}"
export LADDER_DRIVER BUYER_STATE

# Emit both leaves' bundles fresh from the manifest.
emit() { "$REPO/venv/bin/python" tools/emit.py recipes/paid-service-x402 \
  --profile "$PROFILE" --out "$RUNG_DIR/build" "$@"; }
emit --answer offering=generated-text --param "payto_address=$PAYTO" >/dev/null
emit --answer offering=static-file --param "payto_address=$PAYTO" \
     --param "resource_path=$RUNG_DIR/report.txt" >/dev/null
printf 'SCUTL paid report — the coin rang true.\n' > "$RUNG_DIR/report.txt"

run_leaf() {
  local leaf="$1" port="$2" resource="$3"
  local snap="$RUNG_DIR/$leaf/snapshot"
  local bundle="$RUNG_DIR/build/paid-service/$PROFILE"
  echo "=== leaf: $leaf (port $port) ==="
  ./ladder/pserv/setup-snapshot.sh "$snap" "$PAYTO" "$leaf" "$port" "$resource"
  for i in $(seq -w 1 "$REPS"); do
    local rep="$RUNG_DIR/$leaf/rep-$i"
    ./ladder/pserv/run-rep.sh "$rep" "$snap" "$bundle" > "$rep-run.log" 2>&1
    "$REPO/venv/bin/python" - "$rep" "$leaf" "$i" <<'PY'
import json, sys
rep, leaf, i = sys.argv[1:4]
try:
    g = json.load(open(rep + "/grade.json"))
    bad = {k: v for k, v in g["checks"].items() if v is not True}
    print(f"{leaf} rep-{i}: {'GREEN' if g['green'] else 'RED'} {json.dumps(bad)}", flush=True)
except Exception as e:
    print(f"{leaf} rep-{i}: ERROR {e}", flush=True)
PY
  done
}

run_leaf generated-text 8402 ""
run_leaf static-file    8403 "$RUNG_DIR/report.txt"
echo RUNG-COMPLETE
