# 04 — Current State

## Audit Summary

This document describes what Immortality actually is today, verified against the repository code. Claims are backed by specific files, not documentation or aspirations.

**Date of audit:** September 2026

---

## System Status Overview

| Subsystem | Status | Evidence |
|---|---|---|
| CLI frontend | ✅ Working | `main.py` — Rich terminal, prompt_toolkit |
| HUD frontend | ✅ Working | `frontend/immortility_hud.html`, `tools/hud_*.py` |
| Intent router | ✅ Working | `core/router.py` + `core/execution_mode.py` |
| Fast chat path | ✅ Working | `core/llm.py::fast_chat()` |
| Agent tool loop | ✅ Working | `core/action_engine.py::execute_action()` |
| Permission system | ✅ Working | `core/permissions.py` — 4 modes, tested |
| Coding workflow | ✅ Working | `editing/coding_workflow.py`, `core/coding_engine.py` |
| Hybrid RAG retrieval | ✅ Working | `rag/hybrid_search.py` + TurboVec |
| Knowledge graph (AST) | ✅ Working | `knowledge/hierarchical_memory.py` + SQLite |
| Git tools | ✅ Working | `tools/git_tool.py` — read-only + confirmed mutating |
| Browser automation | ✅ Working | `tools/browser_agent.py` Playwright + accessibility tree |
| Voice STT | ✅ Working | `tools/voice_io.py` Whisper (CPU) |
| Voice TTS | ✅ Working | `tools/voice_io.py` Windows SAPI |
| Experience memory | ✅ Working | `memory/experience_memory.py`, `core/self_reflection.py` |
| Session resume | ✅ Working | `memory/session_resume.py` |
| Workflow engine | ✅ Working | `core/workflow_engine.py` + `workflow.db` |
| Checkpoint manager | ✅ Working | `core/checkpoint_manager.py` |
| Model registry | ✅ Working | `models/registry.py` + `config/models.yaml` |
| Model router | ✅ Working | `models/router.py` — VRAM-aware selection |
| Execution kernel | ✅ Working | `core/execution_kernel.py` — cancel/timeout/trace |
| Harness tracing | ✅ Working | `core/harness.py` — JSONL event log |
| Event bus | ✅ Working | `core/event_bus.py` — in-process pub/sub |
| Kimi K3 config | ⚠️ Configured, inactive | `.env.example` has it; `.env` needs NVIDIA_API_KEY |
| Hermes backend | ⚠️ Implemented, inactive | `core/hermes_backend.py` complete; HERMES_API_KEY missing, server missing |
| Vision model | ⚠️ Configured, inactive | `config/models.yaml` spec exists; OLLAMA_VISION_MODEL unset |
| Voice → agent routing | ❌ Not wired | `main.run_speech_to_speech()` calls `fast_chat()` only |
| Task persistence (durable) | ❌ Not built | `workflow.db` exists but no unified Task object |
| Computer control | ❌ Not built | `system_control.py` does brightness/volume only |
| Screen capture | ❌ Not built | No screenshot tool in registry |
| Proactive intelligence | ❌ Not built | `event_bus.py` exists but no event watchers |
| World model | ❌ Not built | Desktop scanner exists; no structured relationship graph |
| Prompt injection defense | ❌ Not built | `security_filters.py` detects secrets in chunks only |

---

## Kimi K3 Integration — Detailed Status

### What Was Implemented
**Configuration (complete):**
- `config/models.yaml` — `brain` spec: `runtime: openai_compat`, `model_id: moonshotai/kimi-k3`, `env_model_var: NVIDIA_MODEL`
- `.env.example` — `IMMORTILITY_LLM_PROVIDER=nvidia`, `NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1`, `NVIDIA_MODEL=moonshotai/kimi-k3`
- `core/llm.py` — `nvidia` provider path: `local_base_url("nvidia")` returns `NVIDIA_BASE_URL`, `local_api_key("nvidia")` reads `NVIDIA_API_KEY`, `local_model(provider="nvidia")` returns `NVIDIA_MODEL`
- `models/registry.py` — `_PROVIDER_MODEL_VARS["nvidia"] = ("NVIDIA_MODEL",)` ✅

**Client code (complete):**
- `core/llm.py::_chat_openai_compat()` handles the `nvidia` provider path using the `openai` Python package — same code path as any OpenAI-compat endpoint
- `models/manager.py::_health_openai()` — health check calls `GET /models` on the NVIDIA base URL

**Tests (passing with mocks):**
- `tests/test_hermes_backend.py::test_nvidia_kimi_provider_configuration` — verifies provider name, base URL, and default model_id with monkeypatched env

### What Is Missing
- `NVIDIA_API_KEY` not set in `.env` → `local_api_key("nvidia")` raises `RuntimeError("NVIDIA_API_KEY not set")` → all Kimi K3 calls fail
- **Fix:** Set `NVIDIA_API_KEY=<your_key>` in `.env`

### Current Behavior Without API Key
- `IMMORTILITY_LLM_PROVIDER=nvidia` in config
- Every model call fails with "NVIDIA_API_KEY not set"
- Falls through to Gemini (if `GEMINI_API_KEY` set) or local Ollama (if running)
- **This is why the system defaults to Ollama Qwythos in practice**

---

## Hermes Backend — Detailed Status

### What Was Implemented
**`core/hermes_backend.py` (complete adapter):**
- `HermesBackend.run()` — async method that:
  1. `POST /v1/runs` with `{input, instructions, model, provider}`
  2. Tries SSE stream via `GET /v1/runs/{id}/events`
  3. Falls back to polling `GET /v1/runs/{id}` until terminal status
  4. Returns `AgentRunResult(status, output, run_id, events, error, error_kind)`
- Error classification: `KIND_RATE_LIMITED`, `KIND_TIMEOUT`, `KIND_UNAVAILABLE`, `KIND_MALFORMED`, `KIND_UNAUTHENTICATED`, `KIND_FAILED`
- `format_hermes_failure()` — user-facing error messages
- `public_event()` — strips secrets from event payloads before logging

**`core/agent_backend.py` (clean Protocol):**
```python
class AgentBackend(Protocol):
    async def run(self, request, *, context, session_id, require_edits, on_event) -> AgentRunResult
```

**`core/action_engine.py` dispatch (correct):**
```python
if cfg.agent_backend == "hermes" and cfg.hermes_api_key and user_input:
    result = await HermesBackend().run(...)
    if result.ok: return result.output
    return format_hermes_failure(result)
# falls through to legacy JSON tool loop
```

**`core/config.py` defaults:**
- `agent_backend: str = "hermes"` ← configured as default
- `hermes_base_url: str = "http://127.0.0.1:8642"` ← expected local port
- `hermes_api_key: str = ""` ← empty → Hermes never activates

**Tests (all passing with mocks):**
- `test_hermes_backend.py` — 8 tests covering: terminal completion, unavailable server, HTTP 429, timeout, malformed JSON, interrupted run, SSE consumption, event callbacks

### What Is Missing
1. `HERMES_API_KEY` not set in `.env` → condition `cfg.hermes_api_key` is falsy → Hermes never runs
2. No Hermes gateway server process exists in this repository. Hermes is an external service that must be running at `http://127.0.0.1:8642`. The adapter calls its `/v1/runs` REST API but nothing in this repo implements that API.
3. The comment in `action_engine.py` says "Start the loopback Hermes gateway (`hermes gateway run`)" — this refers to an external binary that is not part of this project.

### Architectural Implication
**Hermes is an external dependency, not an internal component.** The adapter is complete. The dependency does not exist locally.

**Two options:**
1. Build a minimal local Hermes-compatible gateway that wraps the existing `execute_action()` loop (recommended: low effort, preserves the architecture)
2. Leave Hermes as optional and make `legacy` the explicit default in config

Until option 1 is implemented, `core/config.py` should default `agent_backend = "legacy"` to avoid every action silently falling through the Hermes check.

---

## Legacy Tool Loop — Detailed Status

### What It Is
The legacy tool loop in `core/action_engine.py::execute_action()` is the default execution engine. It is called when either:
- `cfg.agent_backend != "hermes"`, OR
- `cfg.hermes_api_key` is empty (current state)

### How It Works
```
System prompt (tool_system.txt)
    ↓
LLM generates JSON: {"tool": "...", "args": {...}}
    ↓
Parse JSON (core/json_utils.py)
    ↓
Permissions check (core/permissions.py::decide())
    ↓
ALLOW: execute via tool_registry.execute()
CONFIRM: store as pending_action, ask user
DENY: return denial message
    ↓
Append tool result to conversation history
    ↓
LLM next turn OR DONE
    ↓
DONE: verification gate (diff review + CI)
```

### Loop Safety Features (all working)
- Step cap: `action_max_steps_readonly=10`, `action_max_steps_edit=20`
- Read-before-edit enforcement: LLM must `read_file()` before `edit_file()`
- Identical call detection: same tool+args twice → inject "already did this" message
- No-progress detection: loop continues accumulating evidence, not wasting steps
- Verification gate on DONE: unified diff + semantic reviewer + CI checks
- Fallback model: if primary model fails, retry with `IMMORTILITY_FALLBACK_MODEL`
- Force summary: if max steps reached without DONE, synthesize a report from evidence notes

### Current Limitations
- All logic in one 700+ line function — hard to test individual phases
- `state.json` conversation history is the only continuity across resume — no structured task object
- Evidence notes (accumulated during investigation) are lost on process restart

---

## Voice System — Detailed Status

### Working
- `tools/voice_io.py::VoiceIO` singleton
- Whisper `small.en` (CPU) — accurate, offline, ~500MB
- Windows SAPI TTS via `pyttsx3` + `win32com` (interruptible with "stop")
- Barge-in: background thread listens for stop words during TTS
- Mic device auto-detection with fallback chain
- Hallucination filtering for Whisper artifacts
- Transcript correction (Whisper mishears common terms)
- `/talk` command — continuous speech-to-speech loop

### Not Working
- Voice does NOT route to the full agent loop
- `main.run_speech_to_speech()` calls `fast_chat()` only
- Saying "JARVIS, run the tests" gives a spoken answer, not an execution

### Fix Required
One function change in `main.py`: replace `fast_chat()` call with `classify_route()` + full action dispatch.

---

## Model Layer — Current Active Fleet

Based on `config/models.yaml` and `.env.example`:

| Role | Spec | Model ID | Status |
|---|---|---|---|
| brain | `brain` | `moonshotai/kimi-k3` (NVIDIA NIM) | ⚠️ Inactive — needs API key |
| brain-vllm | `brain-vllm` | `Qwen/Qwen3-8B` (WSL vLLM) | ⚠️ Optional |
| brain-fallback | `brain-fallback` | `IMMORTILITY_FALLBACK_MODEL` | ⚠️ Configured if env set |
| code | `coder` | `OLLAMA_CODE_MODEL` | ⚠️ Unset → brain fallback |
| vision | `vision` | `OLLAMA_VISION_MODEL` | ❌ Unset → unavailable |
| embed | `embed` | `BAAI/bge-small-en-v1.5` (CPU) | ✅ Active |
| rerank | `rerank` | `BAAI/bge-reranker-base` (CPU) | ⚠️ Disabled (RERANK_ENABLED=0) |
| asr | `asr` | `faster-whisper small.en` (CPU) | ✅ Active |
| tts | `tts` | `sapi5` | ✅ Active |

**In practice today:** The system runs on local Ollama with `qwythos9b-q4` (Qwythos-9B Q4_K_M) unless `IMMORTILITY_LLM_PROVIDER` is changed.

---

## Memory System — Current Active Stores

| Store | File/DB | Content | Active? |
|---|---|---|---|
| Working | `state.json` | Conversation history, pending action, active project | ✅ |
| Conversation | JSON file | Message history + notes | ✅ |
| Project | JSON file | Project metadata (path, language, framework) | ✅ |
| Experience | `experience.json` + `.jsonl` | Task outcomes with scores | ✅ |
| Outcomes | `outcomes.db` SQLite | Structured outcome records | ✅ |
| Knowledge graph | `knowledge_graph.db` SQLite | Facts, reflections, AST summaries | ✅ |
| Semantic | `.vector_db/turbovec/` | Code chunks (~14k vectors) | ✅ |
| Docs | `.vector_db/turbovec/documentation` | Imported documentation | ✅ |
| Preference | JSON | User preferences | ✅ |
| Session | In-memory dict | Current session state | ✅ (ephemeral) |

**What is NOT stored:**
- Task plans with per-step observations
- Tool call sequences for completed tasks
- Screen state or visual observations
- Application window history

---

## Key Strengths (Preserve These)

1. **The tool loop is real** — it executes actual filesystem, git, terminal, browser operations
2. **The permission system is correctly designed** — below the LLM, 4 modes, destructive always confirmed
3. **The coding workflow is production quality** — Planner→Coder→Reviewer→Executor→Debugger→Reflector
4. **The model abstraction is complete** — capability-aware, VRAM-aware, health-checked
5. **The hybrid RAG is better than ChromaDB** — BM25 + semantic + code graph, 14k chunks indexed
6. **The verification gate is unusual and valuable** — LLMs rarely implement this correctly
7. **Voice I/O is genuinely offline** — no cloud dependency, working barge-in
8. **The HUD is already JARVIS-aesthetic** — face, vitals, event log, permission bars

## Key Weaknesses (Fix These)

1. **NVIDIA_API_KEY not set** — Kimi K3 is configured but inactive
2. **agent_backend defaults to "hermes"** — silently falls through on every request
3. **Voice routes to fast_chat only** — the most visible gap from JARVIS
4. **No durable task object** — tasks die with the process
5. **action_engine.py is 700+ lines** — single function with too many responsibilities
6. **state.json is not a database** — concurrent writes can corrupt state
