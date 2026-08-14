#!/usr/bin/env bash
# Recipe #2 (paid-service) rev-2 public-tls rung: N reps, generated-text,
# REMOTE merchant behind TLS. Offering is pinned to generated-text by the
# rung-matrix design (cst-8ih.7): rev 1's green rungs already cover offering
# branching on both leaves; this rung buys EXPOSURE coverage.
#
#   MERCHANT_SSH=pserv-merchant \
#   MERCHANT_URL=https://pserv.scutl.org/resource \
#   PUBLIC_HOSTNAME=pserv.scutl.org \
#   PAYTO=0x... \
#   BUYER_STATE=~/.scutl/accept-buyer \   # the LIVE buyer; rung-ref/state was tombstoned at cst-8ih.1 close
#   LADDER_DRIVER=ladder/pserv/hermes-drive.sh \
#   RUNG_DIR=~/rung-ref-pub REPS=15 [BUNDLE_PROFILE=standard] \
#   ./ladder/pserv/run-rung-public.sh
set -u
cd "$(dirname "$0")/../.."
REPO="$PWD"

REPS="${REPS:-15}"
RUNG_DIR="${RUNG_DIR:?set RUNG_DIR}"
BUYER_STATE="${BUYER_STATE:?set BUYER_STATE}"
PROFILE="${BUNDLE_PROFILE:-standard}"
PAYTO="${PAYTO:?set PAYTO (must match the merchant box configured payto)}"
MERCHANT_SSH="${MERCHANT_SSH:?set MERCHANT_SSH}"
MERCHANT_URL="${MERCHANT_URL:?set MERCHANT_URL (public resource URL)}"
PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME:?set PUBLIC_HOSTNAME}"
REMOTE_SNAPSHOT="${REMOTE_SNAPSHOT:-/root/.scutl/snapshot-paid-service}"
export LADDER_DRIVER BUYER_STATE MERCHANT_SSH MERCHANT_URL

# Fail fast on a dead buyer: a tombstoned signer state fails every rep with
# per-rep tracebacks instead of one honest error (hit 2026-08-14, e4b rung).
if [ -f "$BUYER_STATE/tombstone.json" ]; then
  echo "BUYER_STATE $BUYER_STATE is revoked (tombstone.json present) — pick the live buyer state" >&2
  exit 1
fi

"$REPO/venv/bin/python" tools/emit.py recipes/paid-service-x402 \
  --profile "$PROFILE" --out "$RUNG_DIR/build" \
  --answer offering=generated-text --answer exposure=public-tls \
  --param "payto_address=$PAYTO" \
  --param "public_hostname=$PUBLIC_HOSTNAME" >/dev/null

# Freeze (or refresh) the rung snapshot from the box configured state.
# Safe when the standing state is clean; each rep resets from this.
./ladder/pserv/remote-snapshot.sh /root/.scutl/paid-service "$REMOTE_SNAPSHOT"

BUNDLE="$RUNG_DIR/build/paid-service/$PROFILE"
echo "=== public-tls rung: $PROFILE, $REPS reps, merchant $MERCHANT_URL ==="
for i in $(seq -w 1 "$REPS"); do
  rep="$RUNG_DIR/rep-$i"
  ./ladder/pserv/run-rep.sh "$rep" "$REMOTE_SNAPSHOT" "$BUNDLE" \
    > "$rep-run.log" 2>&1
  "$REPO/venv/bin/python" - "$rep" "$i" <<'PY'
import json, sys
rep, i = sys.argv[1:3]
try:
    g = json.load(open(rep + "/grade.json"))
    bad = {k: v for k, v in g["checks"].items() if v is not True}
    print(f"public-tls rep-{i}: {'GREEN' if g['green'] else 'RED'} {json.dumps(bad)}", flush=True)
except Exception as e:
    print(f"public-tls rep-{i}: ERROR {e}", flush=True)
PY
done
echo RUNG-COMPLETE
