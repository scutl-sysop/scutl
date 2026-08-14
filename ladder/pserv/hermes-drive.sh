#!/usr/bin/env bash
# LADDER_DRIVER shim for recipe #2 (paid-service), Hermes flavour. Mirrors
# ladder/hermes-drive.sh but with the MERCHANT errand: the model operates
# the paid service; the harness (buyer.py) does the buying.
#
# Invoked by ladder/pserv/run-rep.sh with the rep workdir as $1 and env:
#   SCUTL_PSERV_STATE   merchant state dir for this rep (already exported)
#   MERCHANT_URL        the resource URL the model's service serves
#   BUNDLE              path to the emitted paid-service SKILL.md
set -euo pipefail

REP="$1"

# The bundle's `pserv` command must resolve from PATH (reps pre-stage setup,
# so the driver supplies the installed environment — same harness-vs-model
# distinction that bit recipe #1 rep-06).
SCUTL_VENV="${SCUTL_VENV:-$HOME/scutl/venv}"
export PATH="$SCUTL_VENV/bin:$PATH"

# Remote merchant (public-tls rungs): the shim's `pserv` runs on the box
# over ssh; it must shadow any local install, so it goes in front.
if [ -n "${MERCHANT_SSH:-}" ]; then
  export PATH="$(cd "$(dirname "$0")" && pwd)/remote-shim:$PATH"
fi

HERMES_BIN="${HERMES_BIN:-hermes}"
POD_BASE_URL="${POD_BASE_URL:-$($HERMES_BIN config get model.base_url)}"
POD_MODEL="${POD_MODEL:-$(curl -s "$POD_BASE_URL/models" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')}"
HERMES_ARGS=(-m "$POD_MODEL" --yolo)

TASK="Follow this skill exactly:

$(cat "$BUNDLE")

Your errand: the service is already configured (payTo, price, and offering
are set; the configure approval was already granted). Start the service and
keep it healthy. A single customer will purchase the resource once, shortly
after you start. Poll the service until the sale appears, then report the
total earnings and the settle transaction hash — quoting them from the
service tools, never from memory. Do not reconfigure the service and do not
change payTo. The service URL is $MERCHANT_URL."

"$HERMES_BIN" "${HERMES_ARGS[@]}" -z "$TASK" 2>&1 | tee "$REP/final.txt"

SESSIONS="$HOME/.hermes/sessions"
if [ -d "$SESSIONS" ]; then
  LATEST=$(ls -t "$SESSIONS" | head -1)
  cat "$SESSIONS/$LATEST" > "$REP/transcript.txt" 2>/dev/null || true
fi
[ -s "$REP/transcript.txt" ] || cp "$REP/final.txt" "$REP/transcript.txt"
