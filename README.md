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
| `/auto` | Toggle full autonomous workflow |
| `/exit` | Quit |

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
| `IMMORTILITY_FALLBACK_MODEL` | Optional larger Ollama model for Action Engine escalation after repeated verification failures |

## Not committed (by design)

Local runtime artifacts stay out of git: `venv/`, `.vector_db/`, `state.json`, `logs/`, `workflow.db`, `graphify-out/`, etc. See `.gitignore`.
