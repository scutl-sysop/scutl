#!/usr/bin/env bash
# Ladder pod setup — run ON the rented GPU pod (RunPod, 4090-class).
# Installs a pinned llama.cpp, fetches the rung's model, starts
# llama-server (OpenAI-compatible), and writes env.json for the receipt.
#
#   MODEL_REPO=unsloth/Qwen3.6-27B-GGUF MODEL_FILE=Qwen3.6-27B-Q4_K_M.gguf \
#   ./pod-setup.sh
#
# Headline rung: override MODEL_REPO/MODEL_FILE with the chosen 9B/a3b.
set -euo pipefail

LLAMA_CPP_TAG="${LLAMA_CPP_TAG:?pin a llama.cpp release tag, e.g. b6xxx}"
MODEL_REPO="${MODEL_REPO:-unsloth/Qwen3.6-27B-GGUF}"
MODEL_FILE="${MODEL_FILE:-Qwen3.6-27B-Q4_K_M.gguf}"
CTX="${CTX:-32768}"
PORT="${PORT:-8080}"
WORK="${WORK:-/workspace/ladder}"

mkdir -p "$WORK" && cd "$WORK"

# nvcc lives off-PATH in non-login shells on runpod images
[ -d /usr/local/cuda/bin ] && export PATH="/usr/local/cuda/bin:$PATH"

# --- build deps (runpod images don't all ship cmake) ---------------------
command -v cmake >/dev/null || {
  apt-get update -qq && apt-get install -y -qq cmake build-essential >/dev/null
}

# --- llama.cpp, pinned tag, CUDA build -----------------------------------
[ -d llama.cpp ] || git clone --depth 1 --branch "$LLAMA_CPP_TAG" \
  https://github.com/ggml-org/llama.cpp
if [ ! -x llama.cpp/build/bin/llama-server ]; then
  cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON
  cmake --build llama.cpp/build -j"$(nproc)" --target llama-server
fi
BUILD_HASH=$(git -C llama.cpp rev-parse HEAD)

# --- model, pinned by sha256 ---------------------------------------------
if [ ! -f "$MODEL_FILE" ]; then
  pip install -q "huggingface_hub[cli]" hf_transfer
  # hf_transfer: parallel Rust downloader — saturates the pod link.
  # HF_TOKEN (optional, via env) lifts anonymous-tier throttling.
  HF_HUB_ENABLE_HF_TRANSFER=1 hf download "$MODEL_REPO" "$MODEL_FILE" --local-dir .
fi
MODEL_SHA=$(sha256sum "$MODEL_FILE" | cut -d' ' -f1)

# --- environment record (receipt input) ----------------------------------
python3 - "$@" <<EOF
import json, subprocess
gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version",
                      "--format=csv,noheader"], capture_output=True,
                     text=True).stdout.strip()
json.dump({
    "gpu": gpu,
    "inference_server": "llama.cpp llama-server",
    "llama_cpp_tag": "$LLAMA_CPP_TAG",
    "llama_cpp_commit": "$BUILD_HASH",
    "model_repo": "$MODEL_REPO",
    "model_file": "$MODEL_FILE",
    "model_sha256": "$MODEL_SHA",
    "ctx": $CTX,
}, open("env.json", "w"), indent=2)
EOF
cat env.json

# --- serve ----------------------------------------------------------------
exec llama.cpp/build/bin/llama-server \
  -m "$MODEL_FILE" -c "$CTX" -ngl 999 --host 0.0.0.0 --port "$PORT" \
  --jinja
