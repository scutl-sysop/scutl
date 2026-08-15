#!/usr/bin/env bash
# Create the standard ladder pod via RunPod REST (see POD-RUNBOOK.md and
# bd memory runpod-rest-pod-create). Prints the create response JSON.
#
#   ./ladder/pod-create.sh [name]
#
# Checklist encoded (each item bit us once):
#   - runpod/pytorch *-devel image (nvidia/cuda images boot with no net)
#   - no dockerStartCmd/dockerEntrypoint (overriding eats /start.sh -> no ssh)
#   - 22/tcp exposed at create (ports cannot be added later)
#   - network volume jo8roirsw9 (scutl-ladder-models; forces EU-RO-1)
#   - 60 GB container disk (headline models land pod-local)
set -euo pipefail

KEY_FILE="${RUNPOD_KEY_FILE:-$HOME/.config/runpod.key}"
NAME="${1:-scutl-ladder}"

curl -fsS -X POST https://rest.runpod.io/v1/pods \
  -H "Authorization: Bearer $(cat "$KEY_FILE")" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "name": "$NAME",
  "cloudType": "SECURE",
  "computeType": "GPU",
  "gpuTypeIds": ["NVIDIA GeForce RTX 4090"],
  "gpuCount": 1,
  "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
  "containerDiskInGb": 60,
  "networkVolumeId": "jo8roirsw9",
  "ports": ["22/tcp"]
}
EOF
