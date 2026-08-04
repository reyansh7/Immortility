# vLLM on WSL2 for Immortility

Immortility itself stays on **Windows**. Only the LLM inference server runs in
**WSL2 Ubuntu** and exposes an OpenAI-compatible API on `localhost:8000`.

Official vLLM does not support native Windows — do **not** `pip install vllm`
into the Windows Immortility `venv`.

## One-time setup

1. Install [WSL2](https://learn.microsoft.com/windows/wsl/install) + Ubuntu 22.04/24.04.
2. Install a current **Windows** NVIDIA driver (WSL uses that driver; no separate Linux driver).
3. Open Ubuntu and confirm the GPU:

```bash
nvidia-smi
```

4. From the Immortility repo (mounted under `/mnt/c/...`):

```bash
cd /mnt/c/Users/reyan/OneDrive/Desktop/immortility1
bash scripts/wsl/setup_vllm.sh
```

## Start the server

```bash
bash scripts/wsl/start_vllm.sh
```

Defaults:

| Env | Default |
|-----|---------|
| `VLLM_MODEL` | `Qwen/Qwen3-8B` |
| `VLLM_PORT` | `8000` |
| `VLLM_MAX_MODEL_LEN` | `8192` |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.85` |

If FP16 OOMs on an 8GB card, serve an AWQ/GPTQ build instead, e.g.:

```bash
VLLM_MODEL=Qwen/Qwen3-8B-AWQ bash scripts/wsl/start_vllm.sh
```

(Use whatever Hugging Face id your card can hold — Immortility only needs the
same string in Windows `.env` as `VLLM_MODEL`.)

## Windows Immortility `.env`

```
IMMORTILITY_LLM_PROVIDER=vllm
VLLM_BASE_URL=http://127.0.0.1:8000/v1
VLLM_MODEL=Qwen/Qwen3-8B
```

Swap backends without code changes:

```
# Ollama OpenAI surface
IMMORTILITY_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen3:8b

# Any other OpenAI-compatible server
IMMORTILITY_LLM_PROVIDER=openai
OPENAI_BASE_URL=http://127.0.0.1:9000/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=your-model-id
```

## Smoke test from Windows PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/models
```

Then start Immortility (`.\run.ps1` or `main.py`) and chat / use the HUD as usual.
