# 18 — Implementation Plan

This plan is incremental. Each milestone produces working software. No phase leaves the system in a broken state.

---

## Milestone 0 — Activate Kimi K3 and Fix Default Backend
**Estimated time:** 1 day  
**Prerequisite:** None

### Problem
- `NVIDIA_API_KEY` is not set → Kimi K3 never activates
- `agent_backend` defaults to "hermes" → every agent call silently falls through the Hermes check
- `OLLAMA_VISION_MODEL` is not set → vision reports unavailable

### Changes

**FILES TO MODIFY:**
- `.env` — Add `NVIDIA_API_KEY=<your_key>`, optionally `OLLAMA_VISION_MODEL=qwen2.5vl:7b-instruct-q4_K_M`
- `core/config.py` — Change `agent_backend: str = "hermes"` to `agent_backend: str = "legacy"`

**Rationale for config.py change:** The current default of "hermes" silently falls through on every request because `HERMES_API_KEY` is always empty. This hides the real execution path. Setting "legacy" as the default makes the actual execution path explicit. When Hermes becomes available, users opt in via `IMMORTILITY_AGENT_BACKEND=hermes`.

**FILES TO PRESERVE:** Everything else

**TESTS:**
```bash
python -m models.doctor
python -m pytest tests/test_hermes_backend.py -v
python -m pytest tests/test_model_layer.py -v
```

**ACCEPTANCE CRITERIA:**
- `/doctor` shows `brain: moonshotai/kimi-k3 (ok)` when API key is set
- `_provider()` returns "nvidia" when `IMMORTILITY_LLM_PROVIDER=nvidia`
- All existing tests pass

---

## Milestone 1 — Durable Task Persistence
**Estimated time:** 2 weeks  
**Prerequisite:** Milestone 0

### Problem
Tasks die with the process. There is no unified Task object. The `current_task` dict in AgentState is a loose JSON blob with no schema.

### Files to Create

**`core/task.py`**
```python
# Purpose: Task dataclass with full lifecycle fields
# Dependencies: dataclasses, datetime, enum
# Key classes: Task, TaskStep, Observation, ErrorRecord, VerificationResult, TaskStatus
```

**`core/task_db.py`**
```python
# Purpose: SQLite-backed task persistence (tasks.db)
# Dependencies: sqlite3, core/task.py, core/repo_paths.py
# Key methods: create(), get(), update_status(), record_observation(), complete(), list_active()
```

**`core/task_manager.py`**
```python
# Purpose: High-level task lifecycle (create/checkpoint/resume/cancel)
# Dependencies: core/task.py, core/task_db.py, core/checkpoint_manager.py
# Key methods: create_task(), checkpoint_task(), resume_task(), complete_task()
```

### Files to Modify

**`core/action_engine.py`**
- At start of `execute_action()`: `task = task_manager.create_task(goal=user_input)`
- After each tool execution: `task_manager.record_observation(task.id, tool_name, args, result)`
- On DONE: `task_manager.complete_task(task.id, final_result)`
- On error: `task_manager.record_error(task.id, error_type, message)`

**`core/config.py`**
- Add `tasks_db_path: str = "tasks.db"` config field

**`main.py`**
- On startup: call `startup_recovery()` to show interrupted tasks
- Add `async def startup_recovery()` function

### Files to Preserve
All other files. Existing workflow_engine.py continues to work as-is. tasks.db is additive.

### Tests
- `tests/test_task_persistence.py` (new):
  - `test_task_create_and_load` — create task, kill process (simulate), reload from DB
  - `test_task_checkpoint_rollback` — modify file, checkpoint, modify again, rollback
  - `test_startup_recovery` — RUNNING task in DB triggers recovery prompt
  - `test_task_observation_recording` — tool calls appear in task observations

**ACCEPTANCE CRITERIA:**
- Create task, kill process, restart → task appears in startup recovery
- Resume task → continues from last recorded step
- Complete task → status = COMPLETED in tasks.db
- All existing tests still pass

---

## Milestone 2 — Computer Control Foundation
**Estimated time:** 2-3 weeks  
**Prerequisite:** Milestone 1

### Problem
Immortality cannot control native Windows applications or capture the screen.

### Dependencies to Add
```
pip install pywinauto==0.6.8
pip install mss==9.0.1
pip install pillow==10.3.0
```

### Files to Create

**`tools/computer_tool.py`**
```python
# Purpose: Windows computer control via UI Automation (pywinauto + UIA)
# Dependencies: pywinauto, mss, pillow
# Key classes: ComputerTool
# Key methods: screenshot(), list_windows(), find_window(), find_element(),
#              click_element(), type_text(), press_key(), launch_app(),
#              close_window(), get_clipboard(), set_clipboard()
```

### Files to Modify

**`tools/tool_registry.py`**
- Register 10 computer control tools in `setup()`
- Add pywinauto import guard (optional dependency — graceful unavailable message if not installed)

**`core/permissions.py`**
- Add `COMPUTER_CONTROL_READ` set (screenshot, list_windows — always allow)
- Add `COMPUTER_CONTROL_MEDIUM` set (click, type, launch — confirm in ASSISTED)
- Add `COMPUTER_CONTROL_HIGH` set (kill_process — always confirm)

**`core/pending_action.py`**
- Add computer tools to `CONFIRMATION_TOOLS` and `READ_ONLY_TOOLS` appropriately

### Files to Preserve
All existing files.

### Tests
- `tests/test_computer_tool.py` (new):
  - `test_screenshot_returns_png` — screenshot() returns valid PNG bytes
  - `test_list_windows_returns_list` — returns at least one window
  - `test_permission_blocks_click_in_safe_mode` — decide("click_element", ..., mode=SAFE) → DENY
  - `test_find_element_notepad` — integration test: launch Notepad, find text area

**ACCEPTANCE CRITERIA:**
- screenshot() returns a valid PNG file
- list_windows() includes the current process
- Permission layer correctly classifies all computer tools
- Notepad integration test: launch → find text area → type text → verify

---

## Milestone 3 — Vision and Screen Understanding
**Estimated time:** 1-2 weeks  
**Prerequisite:** Milestone 2 + vision model pulled in Ollama

### Problem
Immortality cannot analyze what is on screen. Visual computer control fallback doesn't exist.

### Files to Create

**`tools/screen_tool.py`**
```python
# Purpose: Screen understanding via vision model
# Dependencies: mss, pillow, core/llm.py (vision role)
# Key methods: analyze(prompt, region), read_text(region), describe()
```

### Files to Modify

**`tools/tool_registry.py`**
- Register `screen_analyze`, `screen_read_text`, `screen_describe`
- Add vision capability check (graceful unavailable message if OLLAMA_VISION_MODEL unset)

**`tools/computer_tool.py`**
- Add vision fallback path in `find_element()`: if UIA element not found, try `screen_tool.find_element_by_description()`

### Tests
- `tests/test_screen_tool.py` (new):
  - `test_analyze_requires_vision_model` — graceful error when vision unavailable
  - `test_analyze_with_vision_model` — integration: requires OLLAMA_VISION_MODEL set

**ACCEPTANCE CRITERIA:**
- `screen_analyze("what is on screen?")` returns a description
- Vision model eviction/reload cycle works correctly
- Graceful unavailable message when no vision model configured

---

## Milestone 4 — Voice Routing to Full Agent Loop
**Estimated time:** 3-5 days  
**Prerequisite:** Milestone 1 (task persistence needed for voice-triggered tasks)

### Problem
Voice input routes to `fast_chat()` only. "JARVIS, run the tests" talks about running tests, doesn't run them.

### Files to Modify

**`main.py`**
```python
# In run_speech_to_speech():
# BEFORE:
reply = await asyncio.to_thread(fast_chat, heard, ...)

# AFTER:
if _looks_like_fast_chat(heard):
    reply = await asyncio.to_thread(fast_chat, heard, ...)
else:
    await voice_acknowledge("On it.")
    route = await classify_route(heard)
    strategy = classify_strategy(heard, category=route)
    reply = await dispatch_to_agent(heard, route, strategy, memory)
```

**`tools/voice_io.py`**
- Add `speak_brief(message: str)` method for short progress acknowledgments
- Add callback support: `on_tool_start`, `on_task_complete`

### Tests
- Add to `tests/test_voice_chat.py`:
  - `test_voice_routes_to_agent_for_action_intent` — "run the tests" → AGENT not FAST
  - `test_voice_routes_to_fast_for_simple_question` — "what time is it" → FAST

**ACCEPTANCE CRITERIA:**
- Voice command "run the tests" triggers `run_command` with pytest
- Voice command "what's 2+2" still uses fast path
- Spoken acknowledgment before long agent tasks

---

## Milestone 5 — Personal Context and World Model Foundation
**Estimated time:** 2-3 weeks  
**Prerequisite:** Milestone 1

### Problem
Agents don't know who the user is or what their environment looks like. Every task starts with minimal context.

### Files to Create

**`memory/personal_context.py`**
```python
# Purpose: Unified personal context builder for agent calls
# Dependencies: memory/, knowledge/engine.py
# Key method: build_personal_context(query) → structured context string
```

**`memory/world_model.py`**
```python
# Purpose: World model graph operations
# Dependencies: knowledge/knowledge_graph_db.py
# Key methods: get_node(), create_node(), update_node(), get_edges(),
#              add_edge(), get_active_application(), get_recent_tasks()
```

### Files to Modify

**`knowledge/knowledge_graph_db.py`**
- Add world model tables: `world_nodes`, `world_edges`
- Add migration from old schema if needed

**`core/action_engine.py`**
- At start of `execute_action()`: inject personal context into system prompt

**`knowledge/engine.py`**
- Add `get_personal_context(query: str) → str` method

### Tests
- `tests/test_world_model.py` (new):
  - `test_node_create_and_retrieve`
  - `test_edge_create_and_query`
  - `test_personal_context_includes_preferences`

**ACCEPTANCE CRITERIA:**
- Personal context block appears in agent system prompt
- World model correctly stores and retrieves project nodes
- User name from user_profile appears in agent context

---

## Milestone 6 — JARVIS MVP Integration
**Estimated time:** 2 weeks  
**Prerequisite:** Milestones 1-5

### Target Demonstration
> Voice: "JARVIS, open my Immortality project, run the tests, investigate which ones fail, fix the safe issues, rerun, and tell me what changed."

### Files to Create

**`core/jarvis_loop.py`**
```python
# Purpose: Top-level JARVIS orchestrator
# Wires: voice → intent → task → execution → voice report
# Key method: async run_jarvis_turn(voice_input: str) -> str
```

### Files to Modify

**`main.py`**
- Integrate `JarvisLoop` as the primary turn handler
- Add wakeword polling (optional `/wake` command)

### Tests
- `tests/test_jarvis_mvp.py`:
  - Full integration test with mocked voice I/O
  - Verifies: task created → project opened → tests run → result reported

**ACCEPTANCE CRITERIA:**
- Complete the demonstration task from voice to spoken report
- Task is persisted and resumable if interrupted
- All tool calls go through permission layer
- HUD shows task timeline during execution

---

## Files Not to Touch

These files are working correctly and should not be modified unless a specific bug requires it:

```
rag/vector_store.py        → TurboVec integration, working
rag/hybrid_search.py       → BM25+semantic, working
rag/embeddings.py          → BGE-small, working
knowledge/engine.py        → KnowledgeEngine, working (add methods only)
models/registry.py         → No changes needed
models/router.py           → No changes needed
models/manager.py          → No changes needed
editing/coding_workflow.py → Working, no changes
tools/browser_agent.py     → Working, no changes
tools/voice_io.py          → Mostly preserve, add callbacks only
core/harness.py            → Working, no changes
core/event_bus.py          → Working, no changes
core/permissions.py        → Additive changes only (computer control)
frontend/immortility_hud.html → Wire new endpoints, don't redesign
```
