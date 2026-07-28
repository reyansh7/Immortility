# Immortility

Local AI coding assistant that runs on your machine via Ollama. It indexes projects into ChromaDB + a code graph, routes with retrieved context, and can edit code through a tool loop with a verification gate.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `qwen3:8b` pulled (`ollama pull qwen3:8b`)
- Optional: Playwright browsers for web tools (`playwright install`)

## Setup

```powershell
cd immortility1
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

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
| `/open <path>` | Index a project (all supported files → vector DB) |
| `/memory` | Knowledge engine stats |
| `/projects` | Remembered projects |
| `/import-docs <name> <path>` | Import documentation into a separate collection |
| `/clear` | Clear conversation / pending state |
| `/hud` | Re-open the red Immortility JARVIS-style HUD in your browser |
| `/talk` or `/voice` | Continuous speech-to-speech with **local qwen3:8b only** (Whisper → Ollama → male Windows TTS). Say “stop” to leave. |
| `/listen` | One-shot mic input into the normal text loop |
| `/exit` | Quit |

### Offline voice (your model only)

No Hugging Face speech-to-speech / no OpenAI key. Flow:

`mic → Whisper (local) → Ollama qwen3:8b → Windows male TTS`

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

1. **`/open`** walks the project tree, chunks every supported source file, and stores embeddings in ChromaDB. It also builds a Graphify graph when available.
2. **Retrieval** (before routing and in Chat/Action/Project modes) pulls the top relevant chunks + graph relationships — not the entire repo into one prompt.
3. **Deep file reads** use the Action Engine `read_file` tool for full file contents when you point at a specific path.
4. **Memory** stores preferences, project metadata, and auto-learned facts from your messages (after each turn).

Honest limit: an 8B model cannot hold every line of a large multi-folder project in a single context window. Immortility indexes everything, then retrieves the relevant slices. For whole-file analysis, ask it to open/read specific files.

## Architecture (high level)

```
CLI → Knowledge Orchestrator (hybrid retrieval + graph)
    → Intent Router
        → Chat / Project / Action / Workflow / Coding
```

Action Engine `DONE` path runs a verification gate (syntax / build / tests + semantic review) before reporting success.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/audit_report.py
```

## Config

| Env var | Purpose |
|---------|---------|
| `GEMINI_API_KEY` | Google Gemini API key (primary LLM when set) |
| `GEMINI_MODEL` | Gemini model id (default `gemini-2.0-flash`) |
| `IMMORTILITY_LLM_PROVIDER` | `gemini` / `ollama` / `auto` |
| `OLLAMA_MODEL` / `IMMORTILITY_FALLBACK_MODEL` | Local Ollama fallback (kept installed) |
| `OLLAMA_NUM_CTX` | KV cache size. `16384` suits `qwen3:8b` on 8 GB VRAM |
| `WHISPER_MODEL` | `small.en` (default) is far more accurate than `base`/`tiny` |
| `WHISPER_DEVICE` | `cpu` (default) or `cuda`. GPU is probed at startup and falls back to CPU if unusable |
| `IMMORTILITY_CHROME_PROFILE` | Force which Chrome profile opens sites |

Copy `.env.example` → `.env` and set your key. `.env` is gitignored.

## Not committed (by design)

Local runtime artifacts stay out of git: `venv/`, `.vector_db/`, `state.json`, `logs/`, `workflow.db`, `graphify-out/`, etc. See `.gitignore`.
