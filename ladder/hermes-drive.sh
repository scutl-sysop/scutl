#!/usr/bin/env bash
# LADDER_DRIVER shim for Hermes (the modal local-harness user config).
#
# Assumption (Conway, 2026-08-12): bone-stock Hermes config, model pointed
# at whatever llama-server on the pod reports, via the `-model` CLI flag.
# If `-model` can't carry the endpoint, it's ~4 lines in the default
# config instead — adjust HERMES_ARGS below on run day, nothing else.
#
# Invoked by run-rep.sh with the rep workdir as $1 and env:
#   SCUTL_STATE   signer state dir for this rep (already exported)
#   RESOURCE_URL  the x402-priced resource to buy
#   BUNDLE        path to the emitted SKILL.md
#   PAYMENT_ID    payment id the agent must use
set -euo pipefail

REP="$1"

HERMES_BIN="${HERMES_BIN:-hermes}"
# llama-server reports its loaded model at /v1/models; bone-stock Hermes
# + -model <that id>, endpoint override as needed on run day.
POD_BASE_URL="${POD_BASE_URL:?e.g. http://<pod>:8080/v1}"
POD_MODEL="${POD_MODEL:-$(curl -s "$POD_BASE_URL/models" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')}"
HERMES_ARGS=(${HERMES_ARGS:--model "$POD_MODEL"})

# Inline the bundle rather than assuming the harness has file tools; the
# emitted SKILL.md *is* the system context for the rung.
TASK="Follow this skill exactly:

$(cat "$BUNDLE")

Your errand: buy the resource at $RESOURCE_URL using payment id
$PAYMENT_ID, then report the settle transaction hash. Setup is already
done (signer installed, keyed, funded) — start from 'Paying for a
resource' / the Execute loop."

"$HERMES_BIN" "${HERMES_ARGS[@]}" "$TASK" 2>&1 | tee "$REP/transcript.txt"
