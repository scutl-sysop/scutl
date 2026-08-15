#!/usr/bin/env bash
# LADDER_DRIVER shim for recipe #3 (provision), Hermes flavour. Mirrors
# ladder/pserv/hermes-drive.sh: the model runs the provisioning errand
# against the rep's mock provider through the real `prov` tool.
#
# Invoked by ladder/prov/run-rep.sh with the rep workdir as $1 and env:
#   SCUTL_PROV_STATE   prov state dir for this rep (already exported)
#   SCUTL_PROV_API     the rep's mock provider URL
#   BUNDLE             path to the emitted provision SKILL.md
#   LEAF               ip-only | delegated-subzone
set -euo pipefail

REP="$1"
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"

# hermes tool shells re-source the login profile and REBUILD PATH (see
# ladder/pserv/hermes-drive.sh) — the binding is installed in ~/.local/bin.
# prov-approve is never exposed to the model.
mkdir -p "$HOME/.local/bin"
rm -f "$HOME/.local/bin/prov-approve"
ln -sfn "$REPO/venv/bin/prov" "$HOME/.local/bin/prov"

HERMES_BIN="${HERMES_BIN:-hermes}"
POD_BASE_URL="${POD_BASE_URL:-$($HERMES_BIN config get model.base_url)}"
POD_MODEL="${POD_MODEL:-$(curl -s "$POD_BASE_URL/models" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')}"
HERMES_ARGS=(-m "$POD_MODEL" --yolo)

. "$DIR/errand.sh"   # sets $TASK from $BUNDLE and $LEAF

# Only a session written DURING this rep may serve as its transcript
# (rung-ref-pub rep-01 lesson, same as pserv).
STAMP=$(mktemp)
trap 'rm -f "$STAMP"' EXIT

"$HERMES_BIN" "${HERMES_ARGS[@]}" -z "$TASK" 2>&1 | tee "$REP/final.txt"

. "$REPO/ladder/capture-transcript.sh"
capture_transcript "$REP" "$STAMP"
