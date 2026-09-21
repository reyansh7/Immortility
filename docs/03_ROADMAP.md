# 03 — Roadmap

## Roadmap Philosophy

Phases are not rewrites. Each phase is additive — building on the previous phase without removing working systems.

The sequence is determined by dependency, not ambition. A later phase that depends on an earlier one cannot be started until the earlier phase is complete and stable.

Every phase ends with working software. There are no "refactoring phases" that leave the system broken.

---

## Phase 0 — Activate the Existing Infrastructure
**Status:** Ready to execute (config changes only)

**Objective:** Make Kimi K3, Hermes, and Vision capability actually active in the running system.

### What Exists
- `config/models.yaml` has Kimi K3 as the primary brain
- `core/hermes_backend.py` has a complete HTTP adapter
- `core/llm.py` has the NVIDIA provider path
- `IMMORTILITY_AGENT_BACKEND=hermes` is the default in config.py

### Work Required
- Set `NVIDIA_API_KEY` in `.env`
- Set `OLLAMA_VISION_MODEL=qwen2.5vl:7b-instruct-q4_K_M` in `.env` (after pulling model)
- Change `agent_backend` default from `"hermes"` to `"legacy"` in `core/config.py` until Hermes server exists (prevents silent fallthrough on every request)
- Run `/doctor` to verify the full fleet

### Files Modified
- `.env` (API keys)
- `core/config.py` (default agent_backend)

### Acceptance Criteria
- `python -m models.doctor` shows `brain: moonshotai/kimi-k3 (ok)`
- `capability_available("vision")` returns True
- `/capabilities` shows accurate state
- All existing tests pass

### Risk: Low

---

## Phase 1 — Durable Task Persistence
**Status:** Not started

**Objective:** Tasks survive process restarts. JARVIS knows what it was doing and can resume.

### What Exists
- `workflow.db` (SQLite) with workflow state
- `checkpoint_manager.py` with file snapshot/rollback
- `session_resume.py` with conversation handoff
- `core/harness.py` with execution tracing

### What to Build
- `core/task.py` — `Task` dataclass: id, goal, status, plan, steps, current_step, agent, observations, errors, retries, permissions, artifacts, checkpoints, verification, final_result, timestamps
- `core/task_db.py` — SQLite task store (tasks.db), thread-safe, atomic updates
- `core/task_manager.py` — task CRUD, checkpoint integration, startup recovery

### Wiring Changes
- `core/action_engine.py` — create a Task record for every `execute_action()` call, record each tool result as an observation
- `core/workflow_engine.py` — wrap WorkflowState with Task for richer schema
- `main.py` — on startup: query tasks WHERE status='running', offer resume to user

### Files Created
- `core/task.py`
- `core/task_db.py`
- `core/task_manager.py`

### Files Modified
- `core/action_engine.py`
- `core/workflow_engine.py`
- `main.py`

### Tests
- Task creation and SQLite persistence
- Task save → restart → load → same state
- Checkpoint rollback restores files correctly
- Startup recovery offers interrupted task

### Acceptance Criteria
- Kill the process mid-task. Restart. System detects the interrupted task and offers resume.
- Resume continues from the last completed step, not from the beginning.

### Risk: Medium — touches action_engine.py critical path
### Prerequisite: Phase 0

---

## Phase 2 — Computer Control Foundation
**Status:** Not started

**Objective:** Immortality can capture the screen, enumerate windows, and interact with Windows applications via accessibility APIs.

### What Exists
- `tools/system_control.py` — brightness/volume via ctypes keybd_event and WMI
- `tools/browser_tool.py` — Playwright browser control

### What to Build
- `tools/computer_tool.py` — `ComputerTool` class:
  - `screenshot()` — full screen or region capture (mss + PIL)
  - `list_windows()` — enumerate open windows with titles/pids
  - `find_window(title_pattern)` — locate a window by name
  - `get_focused_window()` — what is currently active
  - `find_element(window, role, name)` — UIA accessibility element
  - `click_element(element)` — click via UIA
  - `type_text(element, text)` — type into element via UIA
  - `launch_app(name_or_path)` — start an application
  - `close_window(window)` — close a window
  - `get_clipboard()` / `set_clipboard(text)` — clipboard access

### Dependencies
- `pip install pywinauto mss pillow`
- pywinauto uses Windows UI Automation COM APIs — no external service

### Files Created
- `tools/computer_tool.py`

### Files Modified
- `tools/tool_registry.py` — register 8-10 computer control tools
- `core/permissions.py` — add COMPUTER_CONTROL policy (screenshot=LOW, click=MEDIUM, launch=MEDIUM, kill=HIGH)
- `core/pending_action.py` — add computer tools to confirmation tables

### Tests
- `screenshot()` returns a valid PNG
- `list_windows()` returns at least one window (pytest itself)
- `find_element` with Notepad: launch, find text area, type, verify
- Permission layer blocks `kill_process` in SAFE mode

### Acceptance Criteria
- JARVIS can take a screenshot and save it
- JARVIS can find and click a button in Notepad
- All computer tool calls go through the permission layer

### Risk: Medium — new hardware interaction, Windows-specific
### Prerequisite: Phase 0

---

## Phase 3 — Vision and Screen Understanding
**Status:** Not started

**Objective:** Immortality can look at the screen and understand what it sees.

### What Exists
- `models/router.py` — vision role routing (already defined)
- `config/models.yaml` — vision spec (already declared, model_id unset)
- `models/manager.py` — vision model eviction on heavy model switch

### What to Build
- `tools/screen_tool.py` — `ScreenTool`:
  - `screen_analyze(prompt, region=None)` — capture + vision model query
  - `screen_find_element(description)` — "find the red button" → coordinates/element
  - `screen_read_text(region=None)` — OCR for text extraction

### Files Created
- `tools/screen_tool.py`

### Files Modified
- `tools/tool_registry.py` — register screen analysis tools
- `tools/computer_tool.py` — fallback: if UIA element not found, try vision

### External Requirements
- `ollama pull qwen2.5vl:7b-instruct-q4_K_M` (or equivalent)
- Set `OLLAMA_VISION_MODEL=qwen2.5vl:7b-instruct-q4_K_M` in `.env`

### Acceptance Criteria
- `screen_analyze("what application is in focus?")` returns a meaningful description
- Vision model evicts brain model and reloads correctly
- Brain model reloads after vision task completes

### Risk: Medium — depends on vision model quality
### Prerequisite: Phase 2

---

## Phase 4 — Voice Routing to Full Agent Loop
**Status:** Not started (trivial change)

**Objective:** Speaking "JARVIS, run the tests" actually runs the tests, not just describes them.

### What Exists
- `tools/voice_io.py` — complete Whisper STT + SAPI TTS
- `main.run_speech_to_speech()` — currently routes to `fast_chat()` only
- Full agent loop: `classify_route()` + `execute_action()` (exists)

### What to Change
- `main.py::run_speech_to_speech()` — route through full `classify_route()` + action dispatch, not just `fast_chat()`
- `tools/voice_io.py` — add progress verbalization callback during tool execution
- `main.py` — add spoken acknowledgement ("Working on it") before long agent tasks

### Files Modified
- `main.py`
- `tools/voice_io.py`

### Tests
- Voice input "run the tests" triggers `run_command` with pytest
- Voice input "what's on screen" triggers screen_analyze
- Short voice questions still use fast_chat path

### Acceptance Criteria
- Full voice → classify → execute → speak result pipeline works end-to-end
- Voice commands trigger the same behavior as typed equivalents

### Risk: Low — mostly wiring existing components
### Prerequisite: Phase 1

---

## Phase 5 — Persistent Memory and World Model
**Status:** Not started

**Objective:** Immortality has a living model of the user's digital environment that persists, updates, and is queried in every decision.

### What Exists
- `memory/` — 7 memory subsystems (all working)
- `knowledge/knowledge_graph_db.py` — SQLite facts/reflections
- `knowledge/engine.py` — KnowledgeEngine with cross-store retrieval

### What to Build
- `memory/world_model.py` — structured graph of user's environment:
  - Nodes: User, Project, File, Application, Website, Person, Task, Goal
  - Edges: owns, works_on, uses, depends_on, created, completed
  - Temporal state: last_seen, last_modified, active/inactive
- `memory/personal_context.py` — unified personal context builder that synthesizes from all memory stores into a structured block for injection into every agent call

### Files Created
- `memory/world_model.py`
- `memory/personal_context.py`

### Files Modified
- `knowledge/knowledge_graph_db.py` — add world model tables
- `core/action_engine.py` — inject personal context into system prompt
- `knowledge/engine.py` — `get_personal_context(query)` method

### Acceptance Criteria
- After 5 tasks, asking "what projects have I worked on this week?" returns accurate data
- Personal context is injected in every agent call
- World model updates when tasks complete

### Risk: Medium — touches knowledge graph schema
### Prerequisite: Phase 1

---

## Phase 6 — JARVIS MVP: End-to-End Voice-Driven Task Execution
**Status:** Not started

**Objective:** The first real JARVIS milestone. Voice → plan → tools → computer control → observation → verify → spoken report.

### Target Demonstration Task
> "JARVIS, open my Immortality project, run the tests, investigate which ones fail, fix the safe issues, rerun the tests, and tell me exactly what changed."

This requires: Phase 1 (task persistence) + Phase 2 (computer control) + Phase 4 (voice routing) + Phase 5 (context)

### What to Build
- `core/jarvis_loop.py` — top-level JARVIS orchestrator:
  - Wakeword detection (optional, can use /talk)
  - Voice intent → task creation
  - Task execution with spoken progress updates
  - Completion report via voice + HUD

### Files Created
- `core/jarvis_loop.py`

### Files Modified
- `main.py` — integrate JarvisLoop as optional startup mode

### Acceptance Criteria
- Complete the demonstration task via voice from start to finish
- Task is persisted and resumable if interrupted
- All tool calls go through permission layer
- Spoken progress narration during execution

### Risk: High — integration of many components
### Prerequisite: Phases 1-5

---

## Phase 7 — Proactive Intelligence
**Status:** Not started

**Objective:** Immortality notices things and surfaces actions without being asked.

### What to Build
- `core/event_monitor.py` — EventMonitor + EventWatcher protocol
- `core/watchers/` — concrete watchers:
  - `git_watcher.py` — poll `git fetch` + status for new commits/PRs
  - `schedule_watcher.py` — time-based triggers (like cron)
  - `file_watcher.py` — file system events (builds on existing watchdog)
  - `build_watcher.py` — monitor build output files for failures

### Files Created
- `core/event_monitor.py`
- `core/watchers/git_watcher.py`
- `core/watchers/schedule_watcher.py`
- `core/watchers/file_watcher.py`
- `core/watchers/build_watcher.py`

### Files Modified
- `main.py` — start EventMonitor as background asyncio task
- `tools/hud_notify.py` — wire to task completion + proactive events

### Acceptance Criteria
- A git push to the active project triggers a notification
- A scheduled "daily summary" task runs at the configured time
- Proactive actions go through the same permission layer as user-requested actions

### Risk: Medium — new infrastructure but self-contained
### Prerequisite: Phase 1 (task persistence)

---

## Phase 8 — Learning and Procedural Memory
**Status:** Not started (experience memory partially exists)

**Objective:** Immortality gets demonstrably better at the user's specific workflows over time.

### What Exists
- `memory/experience_memory.py` — records task outcomes
- `memory/experience_dataset.py` — builds JSONL training dataset
- `core/self_reflection.py` — extracts lessons after each task

### What to Build
- Procedural memory extractor: identifies repeated patterns across experience records
- Skill crystallization: "when the user asks to fix tests, always start with git_status" → stored as a skill
- Retrieval-augmented procedure injection: relevant past procedures injected into agent context
- (Future) LoRA fine-tuning pipeline using the experience dataset

### Files Created
- `memory/procedural_memory.py`
- `memory/skill_extractor.py`

### Files Modified
- `core/action_engine.py` — inject relevant procedures into agent context
- `memory/memory_manager.py` — add procedural memory to unified interface

### Acceptance Criteria
- After 20 similar tasks, the system noticeably uses learned patterns
- Procedure retrieval is measurably faster than re-planning from scratch

### Risk: High — quality of extracted procedures varies
### Prerequisite: Phase 5 (world model for context)

---

## Phase 9 — Advanced Observability and Evaluation
**Status:** Partial (harness exists)

**Objective:** Every task is fully traceable. Concrete metrics show whether Immortality is improving.

### What Exists
- `core/harness.py` — TraceEvent recording to JSONL
- `logs/immortility_events.jsonl` — event log

### What to Build
- Task timeline UI in HUD (per-task view: steps, tool calls, timings, outcomes)
- Evaluation harness: a set of benchmark tasks with ground-truth outcomes
- Metrics: task success rate, verification pass rate, retry rate, latency

### Files Modified
- `frontend/immortility_hud.html` — add task timeline panel
- `tests/` — add benchmark task suite

### Acceptance Criteria
- HUD shows full task timeline for any completed task
- Weekly benchmark run produces a comparable metrics report
- Regression in task success rate triggers a review

### Risk: Low
### Prerequisite: Phase 1 (task persistence)

---

## Phase 10 — Security Hardening
**Status:** Partial (permission system exists)

**Objective:** Immortality is safe to run in AUTONOMOUS mode on a real machine.

### What to Build
- Command allowlist/denylist enforcement in `command_tool.py`
- Prompt injection scanner for web content entering agent context
- Secret detection expansion in `rag/security_filters.py`
- Audit log: permanent record of all HIGH-risk actions
- Sandboxed execution option: Docker container for untrusted code

### Files Modified
- `tools/command_tool.py` — allowlist enforcement
- `rag/security_filters.py` — expand secret detection
- `core/permissions.py` — add audit log for HIGH actions

### Files Created
- `security/prompt_scanner.py`
- `security/audit_log.py`

### Acceptance Criteria
- Prompt injection attempt from a malicious webpage is detected and logged
- `rm -rf` in a `run_command` call is rejected before execution
- All HIGH-risk actions appear in the audit log

### Risk: Medium
### Prerequisite: Phase 1

---

## Phase 11 — Advanced General Intelligence Research
**Status:** Research phase

**Objective:** Explore capabilities that approach Level 5 (General Personal Intelligence).

This phase includes:
- Long-horizon autonomous operation (multi-day tasks)
- Cross-device operation (phone, second machine)
- Self-directed knowledge acquisition
- Advanced world modeling with temporal reasoning
- Eventually: LoRA fine-tuning on personal interaction data

**Prerequisite:** All previous phases stable and in production.

---

## Timeline Estimates (Conservative)

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Activate | 1 day | 1 day |
| 1 — Task persistence | 2 weeks | 2 weeks |
| 2 — Computer control | 2-3 weeks | 5 weeks |
| 3 — Vision | 1-2 weeks | 7 weeks |
| 4 — Voice routing | 3 days | 7.5 weeks |
| 5 — World model | 2-3 weeks | 10 weeks |
| 6 — JARVIS MVP | 2-3 weeks | 13 weeks |
| 7 — Proactivity | 2-3 weeks | 16 weeks |
| 8 — Learning | 3-4 weeks | 20 weeks |
| 9 — Observability | 2 weeks | 22 weeks |
| 10 — Security | 2 weeks | 24 weeks |
| 11 — Research | Ongoing | — |
