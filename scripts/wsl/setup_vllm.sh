#!/usr/bin/env bash
# One-time setup for vLLM inside WSL2 Ubuntu (NOT the Windows Immortility venv).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VLLM_VENV:-$HOME/.venvs/immortility-vllm}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Immortility vLLM setup (WSL2)"
echo "    venv: $VENV"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON_BIN not found. Install: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

# NVIDIA GPU visible from WSL?
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
else
  echo "WARNING: nvidia-smi not found. Install/update the Windows NVIDIA driver first,"
  echo "         then reopen this WSL terminal. CPU-only vLLM is too slow for chat."
fi

mkdir -p "$(dirname "$VENV")"
if [[ ! -d "$VENV" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

python -m pip install -U pip wheel setuptools

echo "==> Installing vLLM (this can take several minutes)..."
# Official path: https://docs.vllm.ai/en/latest/getting_started/installation/gpu/
python -m pip install -U vllm

echo
echo "Done. Start the server with:"
echo "  bash $ROOT/start_vllm.sh"
echo
echo "From Windows Immortility (.env):"
echo "  IMMORTILITY_LLM_PROVIDER=vllm"
echo "  VLLM_BASE_URL=http://127.0.0.1:8000/v1"
echo "  VLLM_MODEL=Qwen/Qwen3-8B"
