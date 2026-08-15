#!/usr/bin/env bash
# Recipe #3 (provision) rung: N reps per naming leaf, sequential. The
# manifest requires BOTH blessed leaves (ip-only AND delegated-subzone) in
# a green rung.
#
#   LADDER_DRIVER=ladder/prov/hermes-drive.sh \
#   RUNG_DIR=~/rung-ref-prov \
#   REPS=15 \
#   ./ladder/prov/run-rung.sh
#
# Standard bundle by default (reference rung); BUNDLE_PROFILE=smol for the
# headline rung. No live provider, no card spend — the mock is the wire.
set -u
cd "$(dirname "$0")/../.."
REPO="$PWD"

REPS="${REPS:-15}"
RUNG_DIR="${RUNG_DIR:-$HOME/rung-ref-prov}"
PROFILE="${BUNDLE_PROFILE:-standard}"
export LADDER_DRIVER

# One build dir PER LEAF — a shared --out would let the second emit
# silently overwrite the first leaf's bundle.
emit() { local leaf="$1"; shift; "$REPO/venv/bin/python" tools/emit.py \
  recipes/provision-vultr --profile "$PROFILE" \
  --out "$RUNG_DIR/build-$leaf" --param "api_key_file=~/vultr.key" "$@"; }
emit ip-only --answer naming=ip-only >/dev/null
emit delegated-subzone --answer naming=delegated-subzone \
     --param "dns_subzone=lab.scutl.example" >/dev/null

run_leaf() {
  local leaf="$1"
  local snap="$RUNG_DIR/$leaf/snapshot"
  local bundle="$RUNG_DIR/build-$leaf/provision/$PROFILE"
  local subzone=""
  [ "$leaf" = delegated-subzone ] && subzone="lab.scutl.example"
  echo "=== leaf: $leaf ==="
  ./ladder/prov/setup-snapshot.sh "$snap" "$subzone"
  for i in $(seq -w 1 "$REPS"); do
    local rep="$RUNG_DIR/$leaf/rep-$i"
    ./ladder/prov/run-rep.sh "$rep" "$snap" "$bundle" "$leaf" > "$rep-run.log" 2>&1
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

run_leaf ip-only
run_leaf delegated-subzone
echo RUNG-COMPLETE
