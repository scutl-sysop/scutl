#!/usr/bin/env bash
# One-shot ladder pod bring-up — run ON the rented GPU pod (cst-c7x).
#
# Prefers what the scutl-ladder-models volume already holds (prebuilt
# llama-server, cached model); falls back to download/build only when
# absent. Writes env.json (receipt input), starts llama-server, and
# health-checks it before exiting 0.
#
#   MODEL_FILE=Qwen3.6-27B-Q4_K_M.gguf CTX=65536 ./pod-up.sh
#   MODEL_REPO=unsloth/Qwen3.6-35B-A3B-GGUF \
#   MODEL_FILE=Qwen3.6-35B-A3B-UD-IQ4_XS.gguf ./pod-up.sh
#
# See POD-RUNBOOK.md for the pod-create checklist (image choice, ports,
# volume attach) — those decisions happen before this script can run.
set -euo pipefail

MODEL_REPO="${MODEL_REPO:-unsloth/Qwen3.6-27B-GGUF}"
MODEL_FILE="${MODEL_FILE:?set MODEL_FILE (e.g. Qwen3.6-27B-Q4_K_M.gguf)}"
CTX="${CTX:-65536}"
PORT="${PORT:-8080}"
VOLUME="${VOLUME:-/workspace}"          # network volume mount
WORK="${WORK:-$VOLUME/ladder}"          # volume-backed workdir
LLAMA_CPP_TAG="${LLAMA_CPP_TAG:-b10380}" # only used on fallback build

mkdir -p "$WORK" && cd "$WORK"
[ -d /usr/local/cuda/bin ] && export PATH="/usr/local/cuda/bin:$PATH"

# --- server binary: volume prebuilt first, source build as fallback ------
SERVER=""
for cand in "$WORK/llama.cpp/build/bin/llama-server" \
            "$VOLUME/llama.cpp/build/bin/llama-server"; do
  [ -x "$cand" ] && SERVER="$cand" && break
done
if [ -z "$SERVER" ]; then
  echo "== no prebuilt llama-server on volume; building $LLAMA_CPP_TAG =="
  command -v cmake >/dev/null || {
    apt-get update -qq && apt-get install -y -qq cmake build-essential >/dev/null
  }
  [ -d llama.cpp ] || git clone --depth 1 --branch "$LLAMA_CPP_TAG" \
    https://github.com/ggml-org/llama.cpp
  cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON
  cmake --build llama.cpp/build -j"$(nproc)" --target llama-server
  SERVER="$WORK/llama.cpp/build/bin/llama-server"
fi
BUILD_HASH=$(git -C "$(dirname "$SERVER")/../.." rev-parse HEAD 2>/dev/null || echo "prebuilt-$LLAMA_CPP_TAG")

# --- model: volume cache first, pod-local download as fallback -----------
MODEL_PATH=""
for cand in "$WORK/$MODEL_FILE" "$VOLUME/$MODEL_FILE" "/root/$MODEL_FILE"; do
  [ -f "$cand" ] && MODEL_PATH="$cand" && break
done
if [ -z "$MODEL_PATH" ]; then
  echo "== $MODEL_FILE not cached; downloading (pod-local disk) =="
  pip install -q "huggingface_hub[cli]" hf_transfer
  HF_HUB_ENABLE_HF_TRANSFER=1 hf download "$MODEL_REPO" "$MODEL_FILE" \
    --local-dir /root
  MODEL_PATH="/root/$MODEL_FILE"
  echo "== consider: cp -f $MODEL_PATH $WORK/ before teardown (17-30 GB re-download otherwise) =="
fi
MODEL_SHA=$(sha256sum "$MODEL_PATH" | cut -d' ' -f1)

# --- env.json: receipt input, never hand-written again -------------------
GPU=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)
python3 - <<EOF
import json
json.dump({
    "gpu": """$GPU""".strip(),
    "inference_server": "llama.cpp llama-server",
    "llama_cpp_tag": "$LLAMA_CPP_TAG",
    "llama_cpp_commit": "$BUILD_HASH",
    "model_repo": "$MODEL_REPO",
    "model_file": "$MODEL_FILE",
    "model_sha256": "$MODEL_SHA",
    "ctx": $CTX,
}, open("$WORK/env.json", "w"), indent=2)
EOF
cat "$WORK/env.json"

# --- serve + health check ------------------------------------------------
nohup "$SERVER" -m "$MODEL_PATH" -c "$CTX" -ngl 999 \
  --host 127.0.0.1 --port "$PORT" --jinja \
  >"$WORK/llama-server.log" 2>&1 &
echo $! > "$WORK/llama-server.pid"

echo "== waiting for model load (large models take minutes) =="
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "== llama-server healthy on 127.0.0.1:$PORT (pid $(cat "$WORK/llama-server.pid")) =="
    echo "== next: ssh -N -f -L 18080:127.0.0.1:$PORT ... from the controller =="
    exit 0
  fi
  kill -0 "$(cat "$WORK/llama-server.pid")" 2>/dev/null || {
    echo "!! llama-server died — tail of log:" >&2
    tail -30 "$WORK/llama-server.log" >&2
    exit 1
  }
  sleep 5
done
echo "!! server not healthy after 10 min — check $WORK/llama-server.log" >&2
exit 1
