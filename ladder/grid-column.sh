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
      --subject-url "$URL" --subject-model "$TAG" \
      > "$out.tmp" 2> "$log"
  rc=$?
  # rc 3 = graded HARD FAIL — a real result, file it; other nonzero = infra
  if [ $rc -ne 0 ] && [ $rc -ne 3 ]; then
    echo "== $bench: runner rc=$rc (infra, not a grade) — see $log =="
    rm -f "$out.tmp"; FAILED+=("$bench"); continue
  fi
  mv -f "$out.tmp" "$out"
  [ -n "$ENVJ" ] && cp -f "$ENVJ" "$REPO/ladder/$bench/env.json"
  "$PY" - "$out" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
print(f"   -> safety={r['safety']} outcome={r.get('outcome_rate')} "
      f"robust={r.get('robustness_rate')} transp={r.get('transparency_rate')}")
EOF
done

echo "== column $TAG done; failed: ${FAILED[*]:-none} =="
[ ${#FAILED[@]} -eq 0 ]
