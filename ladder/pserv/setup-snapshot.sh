#!/usr/bin/env bash
# Produce the config snapshot for a recipe-#2 rung: ONE human-approved
# configure, then a clean state dir (empty earnings/served logs) that each
# rep copies. Run once per rung, per offering leaf.
#
#   ./ladder/pserv/setup-snapshot.sh <snapshot_dir> <payto> <offering> <port> [resource_path]
#
# The configure approval is a human op (pserv-approve configure); this
# script assumes the caller has authority to mint it for the rung (as the
# acceptance run did) and consumes exactly one.
set -euo pipefail

SNAP="$1"; PAYTO="$2"; OFFERING="$3"; PORT="$4"; RESOURCE="${5:-}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$REPO/venv"
export PATH="$VENV/bin:$PATH"
export SCUTL_PSERV_STATE="$SNAP"

rm -rf "$SNAP"
CFG_ARGS=(--payto "$PAYTO" --price 0.01 --offering "$OFFERING"
          --bind-addr 127.0.0.1 --bind-port "$PORT")
[ -n "$RESOURCE" ] && CFG_ARGS+=(--resource-path "$RESOURCE")

pserv-approve configure >/dev/null
pserv admin configure "${CFG_ARGS[@]}"
# Ensure the daemon is down and logs are empty in the snapshot.
pserv stop >/dev/null 2>&1 || true
: > "$SNAP/earnings.log"
: > "$SNAP/served.log"
rm -f "$SNAP/pidfile"
echo "snapshot ready: $SNAP (offering=$OFFERING port=$PORT payto=$PAYTO)"
