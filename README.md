# Immortility

Local AI coding assistant. Inference is **OpenAI-compatible** (vLLM in WSL2 by
default, or Ollama / any other `/v1` server via config). Projects are indexed into
**TurboVec** + a code graph, retrieved with hybrid BM25 + semantic search, and
edited through a tool loop with a verification gate.

## Requirements

- Python 3.11+ (Windows Immortility app)
- **vLLM in WSL2** serving `Qwen/Qwen3-8B` (see [`scripts/wsl/README.md`](scripts/wsl/README.md))  
  — or Ollama / another OpenAI-compatible backend (config swap only)
- Optional: Playwright browsers for web tools (`playwright install`)

## Setup

```powershell
cd immortility1
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Start the LLM server in WSL (once):

```bash
cd /mnt/c/Users/<you>/…/immortility1
bash scripts/wsl/setup_vllm.sh
bash scripts/wsl/start_vllm.sh
```

Windows `.env` (defaults):

```
IMMORTILITY_LLM_PROVIDER=vllm
VLLM_BASE_URL=http://127.0.0.1:8000/v1
VLLM_MODEL=Qwen/Qwen3-8B
```

**Reindex after upgrading from Chroma:** TurboVec cannot read old Chroma files.
Run `/open` on your projects (or `scripts/ingest_projects.py`) once.

Optional graph tooling:

```powershell
uv tool install graphifyy
graphify install
```

## Run

```powershell
.\venv\Scripts\python.exe main.py
# or
.\run.ps1
```

Useful commands inside the session:

| Command | Purpose |
|---------|---------|
| `/open <path>` | Index a project (all supported files → TurboVec) |
| `/memory` | Knowledge engine stats |
| `/projects` | Remembered projects |
| `/import-docs <name> <path>` | Import documentation into a separate collection |
| `/clear` | Clear conversation / pending state |
| `/hud` | Re-open the red Immortility JARVIS-style HUD in your browser |
| `/doctor` | Model fleet preflight: GPU/VRAM, config, per-model health, routing |
| `/capabilities` | Deterministic capability report (not LLM memory) |
| `/talk` or `/voice` | Continuous speech-to-speech (Whisper → local LLM → male Windows TTS). Say “stop” to leave. |
| `/listen` | One-shot mic input into the normal text loop |
| `/exit` | Quit |

### Offline voice (your model only)

No Hugging Face speech-to-speech / no OpenAI cloud STT. Flow:

`mic → Whisper (local) → OpenAI-compatible LLM (vLLM/Ollama) → Windows male TTS`

Everything ships in `requirements.txt`. Start it with `/talk` in the CLI, or tap
the mic in the HUD for a continuous conversation.

Accuracy is not left to chance — Whisper is biased toward Immortility's own
vocabulary before decoding, and repaired afterwards:

- **`hotwords` + priming prompt** seed the decoder with the words you actually
  say: `Immortility`, `YouTube`, `LeetCode`, `Netflix`, `Wikipedia`, plus the
  real folder names on your Desktop.
- **Beam search** (`beam_size=5`) on short phrases instead of greedy decoding.
- **Transcript repair** normalizes what still slips through — `leet code` →
  `LeetCode`, `net flix` → `Netflix`, `immortality` → `Immortility`. Ordinary
  speech is left untouched (see `tests/test_voice_chat.py`).
- **Hallucination filters** drop the `thanks for watching` / repeated-token junk
  Whisper invents on silence, while always keeping short answers like `yes` so
  confirmations still work by voice.
- **Barge-in**: say `stop` while it's talking and TTS is cut off mid-sentence.

Mishearing something specific? Add it to `ALIASES` in `tools/voice_vocab.py`.

## How project understanding works

1. **`/open`** walks the project tree, chunks every supported source file, embeds
   with BGE-small, and stores vectors in **TurboVec** (plus a SQLite sidecar for
   text/metadata). It also builds a Graphify graph when available.
2. **Retrieval** merges semantic (TurboVec) + keyword (BM25). Optional CrossEncoder
   rerank: set `RERANK_ENABLED=1`.
3. **Deep file reads** use the Action Engine `read_file` tool for full file contents when you point at a specific path.
4. **Memory** stores preferences, project metadata, and auto-learned facts from your messages (after each turn).

Honest limit: an 8B model cannot hold every line of a large multi-folder project in a single context window. Immortility indexes everything, then retrieves the relevant slices. For whole-file analysis, ask it to open/read specific files.

## Architecture (high level)

```
CLI/HUD → Knowledge Orchestrator (TurboVec hybrid + graph)
       → Intent Router
           → Chat / Project / Action / Workflow / Coding
Model layer (config/models.yaml) → role → runtime
LLM  ←── OpenAI-compatible HTTP (vLLM in WSL2 / Ollama / custom)
```

Action Engine `DONE` path runs a verification gate (syntax / build / tests + semantic review) before reporting success.

Phase 2B tools are primitives, not agents. Git/Docker go through the same `CommandTool` engine as `run_command` (timeout, cwd, output limits, cancel, traces, permissions). Documents use real parsers (`extract_document`). Databases only accept **named** connections from `config/databases.yaml` or `IMMORTILITY_SQLITE_PATH` / `IMMORTILITY_POSTGRES_URL` / `IMMORTILITY_MONGO_URL`. Destructive git (force-push, `reset --hard`, delete branch) never runs silently.

```
User → router (FAST/AGENT/BACKGROUND) → discover_tools / planner
     → Tool Kernel → Execution Kernel → CommandTool (one subprocess path)
```

Models are chosen per **role** (`brain`, `code`, `vision`, `embed`, `asr`, `tts`) by the
model layer in `models/`, which is VRAM-aware and keeps one heavy model resident at a
time. See [`IMMORTALITY_MODELS.md`](IMMORTALITY_MODELS.md); the full architecture audit
and roadmap are in [`IMMORTALITY_AUDIT.md`](IMMORTALITY_AUDIT.md).

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/audit_report.py
.\venv\Scripts\python.exe -m models.doctor
```

## Config

| Env var | Purpose |
|---------|---------|
| `IMMORTILITY_LLM_PROVIDER` | `vllm` / `ollama` / `openai` / `gemini` / `auto` |
| `VLLM_BASE_URL` / `VLLM_MODEL` | Local vLLM OpenAI endpoint (default Qwen3-8B) |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Ollama `/v1` surface (config swap) |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | Any other OpenAI-compatible server |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Optional Gemini path |
| `IMMORTILITY_FALLBACK_MODEL` | Secondary model on the same local base URL |
| `OLLAMA_CODE_MODEL` / `OLLAMA_VISION_MODEL` | Optional coding / vision specialists (unset = brain handles code, vision unavailable) |
| `IMMORTILITY_NUM_CTX` | Chat context window (8192 default, 16384 tested max on 8 GB) |
| `RERANK_ENABLED` | `1` to enable CrossEncoder rerank (CPU, default off) |
| `WHISPER_MODEL` | `small.en` (default) is far more accurate than `base`/`tiny` |
| `WHISPER_DEVICE` | `cpu` (default) or `cuda`. GPU is probed at startup and falls back to CPU if unusable |
| `IMMORTILITY_CMD_TIMEOUT` | Default command timeout in seconds (kill on expiry; default 60) |
| `IMMORTILITY_CMD_MAX_OUTPUT` | Max stdout/stderr chars from a command (default 200000) |
| `IMMORTILITY_GIT_ALLOWED_ROOTS` | Extra git roots (os.pathsep-separated) besides the Immortility repo and active project |
| `IMMORTILITY_SQLITE_PATH` | Optional named SQLite connection `sqlite_default` |
| `IMMORTILITY_POSTGRES_URL` | Optional named Postgres connection `postgres_default` (read-only unless configured) |
| `IMMORTILITY_MONGO_URL` | Optional named Mongo connection `mongo_default` |

Copy `.env.example` → `.env`. `.env` is gitignored.

Swap models without code changes: change `VLLM_MODEL` (and restart `start_vllm.sh`
with the same Hugging Face id). Examples: Gemma, Qwen3-Coder, Devstral, AWQ builds.

## Not committed (by design)

Local runtime artifacts stay out of git: `venv/`, `.vector_db/`, `state.json`, `logs/`, `workflow.db`, `graphify-out/`, etc. See `.gitignore`.
