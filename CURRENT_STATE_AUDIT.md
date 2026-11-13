# CURRENT STATE AUDIT — IMMORTALITY RUNTIME

**Audit Timestamp:** September 2026  
**Auditor:** Antigravity Engineering Runtime Agent  
**Baseline Test Execution:** `pytest` -> 476 passed, 0 failed, 0 skipped, 1 warning in 81.50s (Python 3.13.11, pytest-9.1.1).

---

## 1. Executive Summary & Verification Baseline

This audit establishes the ground truth of the IMMORTALITY repository prior to implementing the custom harness runtime upgrade. Every claim is verified against actual files and test execution.

* **Actual baseline test count:** 476 passed, 0 failed, 0 skipped.
* **Core architecture style:** Modular monolith in Python (asyncio + SQLite + rich + FastAPI/tools).
* **Previous milestone accomplishments:**
  * Added `Task`, `TaskStep`, `Observation`, `ErrorRecord`, `VerificationResult` (`core/task.py`).
  * Added persistent SQLite `TaskStore` with WAL mode and atomic updates (`core/task_db.py`).
  * Added `TaskManager` lifecycle coordinator (`core/task_manager.py`).
  * Added `ImmortalityHarnessBackend` protocol adapter (`core/immortality_backend.py`).
  * Added task observation recording and error recording hooks into `core/action_engine.py`.
  * Added startup recovery detection (`TaskManager.startup_recover()`).
  * Added 39 unit tests in `tests/test_task_persistence.py`.

---

## 2. Component-by-Component Assessment

### 2.1 Action Engine (`core/action_engine.py`)
* **What exists:**
  * 1,001-line module containing `execute_action()` and `_run_legacy_loop()`.
  * Monolithic step loop (`for step in range(max_steps):`) managing LLM calls, JSON parsing, tool execution, permissions, verification, and reflection.
  * Task lifecycle hooks: creates Task at entry, records observations on tool execution, records errors, and completes/fails task.
  * Safety guards: read-before-edit requirement, identical failure detection (halts if tool fails identically twice), step caps (10 readonly, 20 edit), no-progress counter.
  * Verification gate on DONE: file syntax check, CI gate, unified diff inspection, and semantic reviewer LLM call.
  * Fallback mechanism: switches to local fallback model if configured and verification fails twice.
* **What can be reused:**
  * Path normalization logic (`_normalize_tool_path`, `sanitize_llm_path`).
  * Tool call signature calculation (`_tool_call_sig`).
  * Error detection and hint generators (`_tool_call_failed`, syntax/target text hints).
  * Backward-compatible function signatures for `execute_action` and `resume_confirmed_pending`.
* **What is missing or flawed:**
  * Logic is trapped in a 600-line monolithic function (`_run_legacy_loop`).
  * Context construction is ad-hoc string concatenation without token budgeting.
  * No structured contract between model output, tool request, execution, and observation.
  * Read-before-edit rule is hardcoded to all `EDIT_TOOLS` regardless of tool nature.
  * Verification heavily couples mechanical gates with an LLM reviewer.

### 2.2 Execution Kernel & Harness Tracing (`core/execution_kernel.py`, `core/harness.py`)
* **What exists:**
  * `ExecutionKernel`: Process-wide singleton handling `run_model()` (timeouts, streaming, role-based model routing via `models.router`), `run_tool()` (timeouts, tool cache, permission gate), cancellation (`cancel_current()`), and progress events.
  * `core/harness.py`: Structured event logging emitting `TraceEvent` records to `events_log_path()` (JSONL).
* **What can be reused:**
  * `ExecutionKernel.run_model()` and `ExecutionKernel.run_tool()` provide clean thread-pool / async primitives.
  * `core/harness.py::record(TraceEvent)` provides non-negotiable execution tracing.
* **What is missing:**
  * Kernel knows nothing about multi-step Task state, observation feeding, or iterative replanning.

### 2.3 Task Persistence & Lifecycle (`core/task.py`, `core/task_db.py`, `core/task_manager.py`)
* **What exists:**
  * Clean dataclasses: `Task`, `TaskStep`, `Observation`, `ErrorRecord`, `VerificationResult`.
  * `TaskStatus` enum: `CREATED`, `PLANNING`, `EXECUTING`, `VERIFYING`, `RECOVERING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`, `INTERRUPTED`.
  * `TaskStore`: Thread-safe SQLite store with WAL mode, single-row atomic UPDATEs.
  * `TaskManager`: High-level lifecycle coordinator with `create_task`, `set_plan`, `begin_step`, `complete_step`, `record_observation`, `record_error`, `checkpoint_task`, `rollback_task`, `complete_task`, `fail_task`, `resume_task`, `startup_recover`.
* **What can be reused:**
  * Entire `TaskStore` and `TaskManager` infrastructure is rock-solid.
* **What is missing:**
  * `TaskStatus` lacked explicit `WAITING_PERMISSION` and `WAITING_INPUT` states for interactive consent flows (now added).
  * `TaskManager.rollback_task()` exists but relies on `CheckpointManager` snapshotting file paths; rollback needs clear boundaries (only rollback tools that support undo).
  * Task resume was only a database status change: there was no runtime engine that loaded an interrupted task's prior observations and continued the agent loop.

### 2.4 Backend Layer (`core/agent_backend.py`, `core/immortality_backend.py`, `core/hermes_backend.py`)
* **What exists:**
  * `AgentBackend` Protocol (`run(request, context, session_id, require_edits, on_event) -> AgentRunResult`).
  * `HermesBackend`: Adapter calling external Hermes REST gateway. Inactive because `HERMES_API_KEY` is not set and no server runs on port 8642.
  * `ImmortalityHarnessBackend`: Created by previous agent, but merely called `action_engine._run_legacy_loop()`.
* **What can be reused:**
  * `AgentBackend` protocol and `AgentRunResult`.
* **What is missing:**
  * `ImmortalityHarnessBackend` must invoke a genuine `ImmortalityHarness` runtime engine rather than delegating back to the legacy loop.

### 2.5 Security & Permissions (`core/permissions.py`, `core/pending_action.py`)
* **What exists:**
  * 4 modes: `SAFE` (read-only), `ASSISTED` (mutations confirm), `AUTONOMOUS` (non-destructive auto-runs), `DEVELOPER` (most auto-runs, destructive confirms).
  * `decide(tool_name, args, mode)` returns `PermissionDecision(ALLOW | CONFIRM | DENY)`.
  * Destructive detection (`is_destructive_invocation`), confirmation detection (`needs_confirmation`).
* **What can be reused:**
  * Security boundary is already placed below the model. We reuse `decide()` unconditionally.

### 2.6 Memory Systems (`memory/`)
* **What exists:**
  * `MemoryManager` (`memory/memory_manager.py`): aggregates `ConversationMemory`, `ProjectMemory`, `PreferenceMemory`, `SessionMemory`.
  * `ExperienceMemory` (`memory/experience_memory.py`): SQLite / JSON experience outcomes with scoring and search.
  * `UserProfile` (`memory/user_profile.py`): reads/writes user name, role, preferences.
  * Knowledge graph (`knowledge/hierarchical_memory.py`, `knowledge/engine.py`).
  * Vector store (`rag/hybrid_search.py`, `.vector_db/turbovec/`).
* **What can be reused:**
  * All memory stores are active and populated.
* **What is missing:**
  * A unified query interface for task context injection (budgeted retrieval combining user preferences, project context, and past experience).
  * A dedicated `UserModel` abstraction wrapping user identity and preferences safely.

### 2.7 Verification Layer (`editing/verifier.py`, `editing/ci_gate.py`)
* **What exists:**
  * `Verifier`: static syntax check via `PatchValidator.validate_syntax`, `py_compile`, optional linter, test runner via `verify_project_tests`.
  * `CIGate`: lint -> typecheck -> test execution pipeline.
  * `ProjectVerifier`: file checks -> CI gate -> build verification.
* **What is missing:**
  * Hierarchical verification pipeline separating:
    1. Level 1: Mechanical syntax & schema.
    2. Level 2: Execution / test commands.
    3. Level 3: Unified diff & changed file inspection.
    4. Level 4: Advisory semantic LLM review (never overrides mechanical/test failure).
  * Structured `VerificationResult` generation and persistence against the Task.

---

## 3. Architectural Conflicts & Resolutions

| Conflict / Flaw | Source | Resolution |
|---|---|---|
| Monolithic loop in `action_engine.py` | Legacy implementation | Extract modular components into `core/harness/`: `ContextBuilder`, `HarnessPlanner`, `HarnessToolExecutor`, `HarnessVerifier`, `RecoveryManager`, `HarnessAgentLoop`, `ImmortalityHarness`. Refactor `action_engine.py` to route through `ImmortalityHarness` while retaining exact backward-compatible APIs and test mock targets (`_kernel_chat`, `_run_legacy_loop`). |
| Unconditionally requiring `read_file` before any mutation | `action_engine.py` line 806 | Limit `read_file` requirement strictly to file-editing tools (`edit_file`, `replace_lines`, `insert_before`, etc.) where target line/text matching is required. File creation (`create_file`), commands, and other tools do not require prior read. |
| Incomplete Task resume | `TaskManager.resume_task` | Implement true resume in `ImmortalityHarness.resume_task()`: reloads task, reconstructs observation context, verifies environment, and resumes agent loop from the current step. |
| Fake / undefined rollback | High-level roadmap | Implement bounded, deterministic file rollback backed by `core/checkpoint_manager.py`. Non-rollbackable actions (e.g. web requests, sent signals) are explicitly tagged as non-rollbackable. |
| Hardcoded Kimi provider references | Model layer | Harness interacts with `BrainProvider` contract. Provider defaults to configured brain role via `core.execution_kernel.run_model` / `models.router`. |

---

## 4. Next Step: Phase B (Contracts)

Proceed immediately to Phase B to define clean, typed dataclass contracts:
* `ToolRequest`
* `ToolDecision`
* `ToolExecutionResult`
* `RecoveryDecision`
* `BrainProvider` protocol
* Structured `VerificationResult` enhancements
