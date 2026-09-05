#!/usr/bin/env bash
# One grid-campaign column (cst-j01t step 6, on gpod rails — cst-crqs
# live acceptance): run the public smutbench menu for every bench dir
# that already carries reference reports, against a live subject served
# from a gpod-managed pod, writing ladder/<bench>/<TAG>-public-report.json
# plus the pod's env.json beside each report (provenance-or-no-cell).
#
#   GRID_TAG=gemma-e4b SUBJECT_URL=http://127.0.0.1:18080 \
#   ENV_JSON=/path/to/env.json ./ladder/grid-column.sh [bench ...]
#
# Benches default to the 13 that hold qwen36-27b reference cells. The
# pod lifecycle (gpod create/destroy) is NOT here on purpose — the
# operator owns the rental; this script only drives and files.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
TAG="${GRID_TAG:?set GRID_TAG (e.g. gemma-e4b)}"
URL="${SUBJECT_URL:?set SUBJECT_URL (no /v1 suffix)}"
# vLLM enforces the model field (llama-server ignores it): when the
# served name differs from the column tag, pass SUBJECT_MODEL; files
# and the scoreboard column stay keyed by TAG.
MODEL="${SUBJECT_MODEL:-$TAG}"
EXTRA="${SUBJECT_EXTRA:-}"   # JSON merged into every request (env record)
ENVJ="${ENV_JSON:-}"
SEEDS="${SEEDS:-1,2,3}"

BENCHES=("$@")
if [ ${#BENCHES[@]} -eq 0 ]; then
  BENCHES=(amail beacon bell idbr keep mwallet odom pulse silo sprc sweb wing x402v2)
fi

manifest_for() {  # bench dir name == recipe id in its manifest
  grep -l "^  id: $1\$" "$REPO"/recipes/*/recipe.yaml | head -1
}

FAILED=()
for bench in "${BENCHES[@]}"; do
  out="$REPO/ladder/$bench/$TAG-public-report.json"
  log="$REPO/ladder/$bench/$TAG-public-run.log"
  if [ -s "$out" ]; then
    echo "== $bench: $out exists, skipping (resume semantics) =="
    continue
  fi
  manifest="$(manifest_for "$bench")"
  if [ -z "$manifest" ]; then
    echo "== $bench: NO MANIFEST FOUND, skipping =="; FAILED+=("$bench"); continue
  fi
  echo "== $bench: public menu, seeds $SEEDS, subject $TAG @ $URL =="
  "$PY" -m smutbench.runner --manifest "$manifest" --seeds "$SEEDS" \
      --subject-url "$URL" --subject-model "$MODEL" \
      ${EXTRA:+--subject-payload-extra "$EXTRA"} \
      > "$out.tmp" 2> "$log"
  rc=$?
  # The runner's exit code encodes the GRADE (0 green, 1 outcome<1.0,
  # 3 safety HARD FAIL) — every one of those is a result to file. Infra
  # failure is distinguished by the absence of a parseable report on
  # stdout, never by rc (first column run filed rc=1 as infra and
  # discarded two real grades — this check replaces that mistake).
  if ! "$PY" -c "import json,sys; r=json.load(open(sys.argv[1])); r['safety']" "$out.tmp" 2>/dev/null; then
    echo "== $bench: no parseable report (rc=$rc) — infra, see $log =="
    rm -f "$out.tmp"; FAILED+=("$bench"); continue
  fi
  mv -f "$out.tmp" "$out"
  # per-model env file: the bare env.json belongs to the legacy
  # reference runs; sharing it cross-attributes provenance
  [ -n "$ENVJ" ] && cp -f "$ENVJ" "$REPO/ladder/$bench/$TAG-env.json"
  "$PY" - "$out" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"   -> safety={r['safety']} outcome={r.get('outcome_rate')} "
      f"robust={r.get('robustness_rate')} transp={r.get('transparency_rate')}")
EOF
done

echo "== column $TAG done; failed: ${FAILED[*]:-none} =="
[ ${#FAILED[@]} -eq 0 ]
