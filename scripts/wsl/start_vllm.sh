#!/usr/bin/env bash
# Start an OpenAI-compatible vLLM server for Immortility (WSL2 → localhost:8000).
set -euo pipefail

VENV="${VLLM_VENV:-$HOME/.venvs/immortility-vllm}"
MODEL="${VLLM_MODEL:-Qwen/Qwen3-8B}"
HOST="${VLLM_HOST:-0.0.0.0}"
PORT="${VLLM_PORT:-8000}"
# 8GB cards: keep context modest; raise when you have headroom / use AWQ
MAX_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
GPU_UTIL="${VLLM_GPU_MEMORY_UTILIZATION:-0.85}"

if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "ERROR: venv missing at $VENV"
  echo "Run: bash $(dirname "$0")/setup_vllm.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Serving $MODEL on http://${HOST}:${PORT}/v1"
echo "    max-model-len=$MAX_LEN  gpu-memory-utilization=$GPU_UTIL"
echo "    Immortility (Windows) should use VLLM_BASE_URL=http://127.0.0.1:${PORT}/v1"
echo

exec vllm serve "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GPU_UTIL"
