#!/usr/bin/env bash
# Produce the config snapshot for a recipe-#3 rung: ONE human-approved
# configure + set-key, then a clean state dir each rep copies. Run once
# per rung, per naming leaf.
#
#   ./ladder/prov/setup-snapshot.sh <snapshot_dir> [dns_subzone]
#
# The approvals are human ops (prov-approve); this script assumes the
# caller has authority to mint them for the rung (as the acceptance run
# did) and consumes exactly one of each. The key is a MOCK key — rungs
# talk to ladder/prov/mock_vultr.py, never the live provider.
set -euo pipefail

SNAP="$1"; SUBZONE="${2:-}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="$REPO/venv/bin:$PATH"
export SCUTL_PROV_STATE="$SNAP"

rm -rf "$SNAP"
CFG_ARGS=(--plans vc2-1c-1gb --regions ewr --max-instances 2 --max-hourly 0.018)
[ -n "$SUBZONE" ] && CFG_ARGS+=(--dns-subzone "$SUBZONE")

prov-approve configure >/dev/null
prov admin configure "${CFG_ARGS[@]}" >/dev/null

KEYFILE=$(mktemp)
printf 'mock-vultr-key-%s\n' "$(head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$KEYFILE"
prov-approve set-key >/dev/null
prov admin set-key --key-file "$KEYFILE" >/dev/null

rm -f "$SNAP/instances.log"
echo "snapshot ready: $SNAP (subzone=${SUBZONE:-none})"
