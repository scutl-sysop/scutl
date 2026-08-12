#!/usr/bin/env bash
# Build the ladder controller: a fresh incus container with a PINNED stock
# Hermes install + the scutl checkout. Run on the incus host (dedi-2).
#
#   HERMES_COMMIT=<sha> ./ladder/controller-setup.sh [container-name]
#
# Rationale (Conway, 2026-08-12): the end-to-end-tested process must not
# depend on anyone's personal Hermes installation, and Hermes moves fast —
# the controller is disposable and the Hermes version is pinned here and
# recorded in the receipt env block.
#
# Built 2026-08-12 as `scutl-ladder`: images:debian/13, incusbr0 (NAT,
# outbound-only, no public IP), user `ladder`,
# Hermes v0.20.0 (2026.8.3) @ 3c27eb6234bf91b8ceee9e9071591b31e9b148cb.
set -euo pipefail

NAME="${1:-scutl-ladder}"
HERMES_COMMIT="${HERMES_COMMIT:?pin the hermes-agent commit (release tag sha)}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

incus launch images:debian/13 "$NAME"
sleep 8
incus exec "$NAME" -- sh -c \
  'apt-get update -qq && apt-get install -y -qq git curl python3-venv python3-pip ripgrep >/dev/null'
incus exec "$NAME" -- useradd -m -s /bin/bash ladder

# Stock Hermes, pinned. Installer fetched to a file first, then run.
incus exec "$NAME" -- su - ladder -c \
  'curl -fsSL -o /tmp/hermes-install.sh https://hermes-agent.nousresearch.com/install.sh'
incus exec "$NAME" -- su - ladder -c \
  "bash /tmp/hermes-install.sh --non-interactive --commit $HERMES_COMMIT"

# The only non-stock config: the custom-provider lines (pod URL is set for
# real on run day: hermes config set model.base_url http://<pod>:8080/v1).
incus exec "$NAME" -- su - ladder -c \
  'hermes config set model.provider llamacpp &&
   hermes config set model.base_url http://POD-PLACEHOLDER:8080/v1'

# scutl arrives as a git bundle — the repo is private and the container
# holds no credentials.
git -C "$REPO" bundle create /tmp/scutl.bundle master
incus file push /tmp/scutl.bundle "$NAME/home/ladder/scutl.bundle"
incus exec "$NAME" -- chown ladder:ladder /home/ladder/scutl.bundle
incus exec "$NAME" -- su - ladder -c \
  'git clone -q scutl.bundle scutl && cd scutl &&
   python3 -m venv .venv &&
   .venv/bin/pip install -q -e recipes/wallet-base-sepolia/signer &&
   .venv/bin/pip install -q pytest pyyaml requests &&
   .venv/bin/pytest tests recipes/wallet-base-sepolia/signer/tests -q'

echo "controller $NAME ready — hermes pinned @ $HERMES_COMMIT"
