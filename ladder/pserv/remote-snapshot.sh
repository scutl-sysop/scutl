#!/usr/bin/env bash
# Remote counterpart of setup-snapshot.sh: freeze the merchant box's
# already-configured pserv state as the rung snapshot each rep resets from.
# Run once per rung. Assumes the configure approval was already consumed on
# the box (the real approval gate — as the public-tls install did); this
# script only stops the daemon and empties the per-rep evidence logs.
#
#   MERCHANT_SSH=pserv-merchant ./ladder/pserv/remote-snapshot.sh \
#       [remote_state_dir] [remote_snapshot_dir]
set -euo pipefail

: "${MERCHANT_SSH:?set MERCHANT_SSH (ssh host alias for the merchant box)}"
STATE="${1:-/root/.scutl/paid-service}"
SNAP="${2:-/root/.scutl/snapshot-paid-service}"

ssh -o BatchMode=yes "$MERCHANT_SSH" "
  set -euo pipefail
  pserv stop >/dev/null 2>&1 || true
  rm -rf '$SNAP'
  cp -a '$STATE' '$SNAP'
  : > '$SNAP/earnings.log'
  : > '$SNAP/served.log'
  rm -f '$SNAP/pserv.pid' '$SNAP/pidfile'
  chmod 700 '$SNAP'
"
echo "remote snapshot ready: $MERCHANT_SSH:$SNAP (from $STATE)"
