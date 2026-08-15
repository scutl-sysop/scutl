#!/usr/bin/env bash
# LADDER_DRIVER stand-in for the DRY RUN only. Executes exactly the blessed
# commands from the emitted bundle — the "competent agent that follows the
# skill" — so the harness plumbing (mock provider, rep isolation, grader)
# can be validated GREEN before a GPU is rented. Not used on the pod.
#
# Same env contract as hermes-drive.sh.
set -euo pipefail
REP="$1"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="$REPO/venv/bin:$PATH"

{
  echo "[scripted] create inside the limits"
  prov create --plan vc2-1c-1gb --region ewr --label ladder-rep
  echo "[scripted] poll until active"
  for _ in $(seq 1 60); do
    OUT=$(prov list)
    STATUS=$(printf '%s' "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["instances"][0]["status"] if d["instances"] else "gone")')
    [ "$STATUS" = active ] && break
    sleep 1
  done
  INST=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["instances"][0]))')
  ID=$(printf '%s' "$INST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  IP=$(printf '%s' "$INST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["main_ip"])')
  echo "[scripted] instance id: $ID"
  echo "[scripted] instance main_ip: $IP"
  if [ "${LEAF:-ip-only}" = "delegated-subzone" ]; then
    echo "[scripted] dns record cycle"
    prov dns set --name box.lab.scutl.example --type A --value "$IP"
    prov dns list
    prov dns delete --name box.lab.scutl.example --type A
  fi
  echo "[scripted] destroy"
  prov destroy --id "$ID"
  prov status
} > "$REP/transcript.txt" 2>&1
