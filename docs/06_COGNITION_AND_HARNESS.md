# 06 — Cognition and Harness

## What Is the Harness?

The **harness** is the runtime infrastructure that manages the complete lifecycle of a single intelligence turn: from receiving a request to producing a verified result, with full traceability.

It is not the LLM. It is not the tools. It is the system that coordinates them.

The harness is owned by Immortality. It is not delegated to LangGraph, Hermes, or any external framework. Frameworks may be used as optional backends, but the harness logic and its boundaries belong to this codebase.

---

## The Cognition Loop

Every meaningful request follows this cycle:

```
INPUT
    ↓
CONTEXT ASSEMBLY
    ↓
MEMORY RETRIEVAL
    ↓
REASONING (LLM call via model layer)
    ↓
PLAN / TOOL SELECTION
    ↓
PERMISSION CHECK
    ↓
ACTION (tool execution)
    ↓
OBSERVATION (tool result)
    ↓
VERIFICATION
    ↓
RECOVERY (if failed)
    ↓
MEMORY UPDATE
    ↓
RESULT
```

This cycle repeats for multi-step tasks. Each iteration is one "step" in the tool loop.

---

## Current Harness Implementation

The current harness is split across three layers:

### Layer 1: Execution Kernel (`core/execution_kernel.py`)
The generic primitive layer. Knows nothing about tasks, models, or domains.

**Responsibilities:**
- Model calls: `run_model(messages, role, timeout_s, on_token)` → `KernelResult`
- Tool calls: `run_tool(name, args, timeout_s)` → `KernelResult`
- Parallel tool calls: `run_tools_parallel(calls)`
- Background tasks: `run_task(fn)` → `BackgroundHandle`
- Cancellation: `cancel_current()`, `cancelled()`
- Trace recording: every call emits a `TraceEvent` via `core/harness.py`
- Permission gate: calls `permissions.decide()` before every tool
- Tool result cache: read-only tools are cached per turn

### Layer 2: Action Engine (`core/action_engine.py`)
The reasoning loop. Drives the LLM through a sequence of tool calls until DONE.

**Responsibilities:**
- Assembles the tool system prompt (from `prompts/tool_system.txt`)
- Injects context (RAG context, experience block, framework hints)
- Runs the LLM → parse tool JSON → execute tool → observe → repeat loop
- Enforces read-before-edit (LLM must read a file before editing it)
- Detects no-progress and identical-call loops
- Maintains step caps (10 for readonly, 20 for editing)
- Runs the verification gate on DONE:
  - Unified diff check
  - Semantic reviewer (fresh LLM call, different context)
  - CI checks (pytest, py_compile)
- Force-synthesizes a summary when local models never emit DONE
- Dispatches to Hermes backend when configured

### Layer 3: Coding Engine (`core/coding_engine.py`)
A specialized harness for autonomous coding tasks.

**Responsibilities:**
- Deterministic planner: `infer_plan()` extracts pytest targets from request text
- LLM planner: `plan_coding_task()` via `core/coding_planner.py`
- Hooks system: `BEFORE_PLAN`, `BEFORE_INSPECT`, `BEFORE_EDIT`, `BEFORE_REVIEW`, `BEFORE_EXECUTE`, `AFTER_EXECUTE`, `AFTER_REVIEW`, `BEFORE_FINALIZE`
- Named roles on the same loop: Planner, Coder, Executor, Debugger, Reviewer, Reflector
- Success criteria: every coding task declares how we know it's done (pytest targets + py_compile)
- Executor runs only allowlisted commands: `pytest`, `py_compile` via `allowlisted_check_argv()`
- Bounded retries with debug notes passed to next coder iteration

---

## Harness Invariants

These properties must hold in every version of the harness:

1. **The LLM proposes, the harness decides.** The LLM generates a JSON tool call. The harness executes it — after permission checks. The LLM cannot bypass this.

2. **Every tool call is traced.** `core/harness.py::record(TraceEvent)` is called for every model call, tool call, and task. This cannot be disabled.

3. **DONE requires evidence.** An editing task cannot complete without either:
   - Mutating tools actually executing (verified by tracking `tools_executed ∩ MUTATING_TOOLS`)
   - OR passing the verification gate (diff + reviewer + CI)

4. **Verification is separate from execution.** The reviewer runs in a fresh context (not the coder's history) to avoid confirmation bias.

5. **Failure is observable.** Every failed step produces a log entry. Recovery attempts are bounded.

6. **Context is scoped.** The tool system prompt provides only what is needed for the current step. Personal context is added at the top level, not inside the tool loop.

---

## Task State Machine

Every task should eventually follow this state machine (Phase 1 implementation):

```
CREATED
    ↓
PLANNING
    ↓
EXECUTING
    ↓          → (tool failed, retries < max) → RECOVERING → EXECUTING
VERIFYING
    ↓          → (verification failed, retries < max) → RECOVERING → EXECUTING
COMPLETED
    or
FAILED
    or
CANCELLED
    or
PAUSED (user or system interrupt)
    ↓
RESUMED
    ↓
EXECUTING (from last checkpoint)
```

### Task Schema (Phase 1 target)

```python
@dataclass
class Task:
    id: str                          # UUID
    goal: str                        # Original user request
    status: TaskStatus               # State machine status
    plan: list[TaskStep]             # Planned steps with expected outcomes
    current_step: int                # Index into plan
    agent: str                       # "legacy_loop" | "hermes" | "coding_engine"
    observations: list[Observation]  # Tool results, model outputs
    errors: list[ErrorRecord]        # Failed steps with reasons
    retries: int                     # Total retry count
    permissions: PermissionGrant     # Which permissions were active
    artifacts: list[str]             # File paths created/modified
    checkpoints: list[str]           # Checkpoint directory paths
    verification: VerificationResult | None
    final_result: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

@dataclass
class TaskStep:
    index: int
    description: str
    expected_outcome: str
    tool_calls: list[ToolCallRecord]  # What tools were used
    status: StepStatus                # pending | running | done | failed
    started_at: datetime | None
    completed_at: datetime | None

@dataclass
class Observation:
    step: int
    tool_name: str
    args: dict
    result: str
    success: bool
    timestamp: datetime
```

---

## Model Interface

The harness communicates with models through `core/llm.py::chat()`. The interface is:

```python
def chat(
    model: str = "auto",
    messages: list[dict] = [],
    force_provider: str | None = None,
    role: str = "brain",           # brain | code | vision
    format: str | None = None,     # "json" for structured output
    max_output_tokens: int | None = None,
    temperature: float = 0.4,
) -> dict  # {"message": {"role": "assistant", "content": "..."}}
```

### Model Selection Order
1. Explicit `model` argument (if provided and not "auto")
2. Role specialist from registry (`role="code"` → coder spec if configured)
3. Environment variable for the provider (`NVIDIA_MODEL`, `OLLAMA_MODEL`, etc.)
4. Registry brain spec for the active provider
5. Built-in default (`moonshotai/kimi-k3` for nvidia, `Qwen/Qwen3-8B` for vllm, `qwen3:8b` for ollama)

### VRAM Management
Before every model call, the model manager ensures VRAM room:
- `models/manager.py::ensure_loaded(role)` — evicts the current heavy model if a different heavy role is needed
- Eviction is done via Ollama's `keep_alive=0` unload request
- Light models (embeddings, ASR, TTS) never evict heavy models

### Primary Intelligence Model
Kimi K3 via NVIDIA NIM is the target primary brain:
- 128K context window (vs 8K local)
- OpenAI-compatible API (existing client handles it)
- Strong reasoning, coding, and tool calling
- Configured in `config/models.yaml` as the `brain` spec
- **Currently inactive:** `NVIDIA_API_KEY` not set in `.env`

The harness is designed so that swapping from Kimi K3 to any other provider requires only an `.env` change. No code changes.

---

## Context Management

### Context Window Budget
- Brain (Kimi K3 via NIM): ~128K tokens
- Brain (local Ollama Q4): 8192 default, 16384 max tested on 8GB VRAM
- Code specialist: same as brain role
- Vision model: evicts brain, ~8K context

### Context Assembly Strategy (current: `knowledge/smart_context_builder.py`)

For every agent call, context is assembled in this fixed order:
1. **Project facts** — framework, language, key patterns (from KG)
2. **Repo summary** — architectural description of the active project
3. **Graph symbols** — AST-extracted definitions for relevant symbols
4. **Semantic chunks** — top-4 hybrid BM25+semantic results
5. **Current file state** — if editing a specific file
6. **Recent diff** — if reviewing recent changes
7. **User task** — the original request

### Personal Context (Phase 5 target)
A personal context block is prepended to every agent system prompt:
```
User: {user_name}
Preferences: {key preferences}
Active project: {project name and summary}
Recent tasks: {last 3 task outcomes}
Relevant procedures: {matching procedures from procedural memory}
```

---

## Hooks System (`core/hooks.py`)

The coding engine supports lifecycle hooks at specific points:

| Hook | When | Purpose |
|---|---|---|
| `BEFORE_PLAN` | Before LLM planner | Inject additional context |
| `BEFORE_INSPECT` | Before initial file inspection | Skip or modify inspect scope |
| `BEFORE_EDIT` | Before each coder iteration | Validate pre-conditions |
| `BEFORE_REVIEW` | Before reviewer runs | Inject review criteria |
| `AFTER_REVIEW` | After reviewer returns | Record review outcome |
| `BEFORE_EXECUTE` | Before pytest/py_compile | Validate test targets |
| `AFTER_EXECUTE` | After checks complete | Record check outcome |
| `BEFORE_FINALIZE` | Before marking success | Final validation gate |

Hooks can cancel the operation, skip the step, or inject context. They run deterministically in process, not as external services.

---

## Hermes as an Optional Backend

The `AgentBackend` Protocol (`core/agent_backend.py`) defines the interface:

```python
class AgentBackend(Protocol):
    async def run(
        self,
        request: str,
        *,
        context: str = "",
        session_id: str = "",
        require_edits: bool = False,
        on_event: Any = None,
    ) -> AgentRunResult: ...
```

The current dispatch in `execute_action()`:
```python
if cfg.agent_backend == "hermes" and cfg.hermes_api_key and user_input:
    result = await HermesBackend().run(...)
    if result.ok: return result.output
    return format_hermes_failure(result)
# falls through to legacy loop
```

**The legacy loop IS an AgentBackend.** It should be formalized as `LegacyLoopBackend` implementing the Protocol, making the dispatch symmetric:

```python
backends = {
    "hermes": HermesBackend,
    "legacy": LegacyLoopBackend,  # wraps execute_action()
}
backend = backends.get(cfg.agent_backend, LegacyLoopBackend)()
result = await backend.run(request, context=context_override, ...)
```

This makes Hermes truly optional and the legacy loop explicitly first-class.

---

## Recovery and Checkpointing

### Current Implementation
- `core/checkpoint_manager.py::save()` — snapshots a list of files to `.checkpoints/{workflow_id}_{ts}/files/`
- `core/checkpoint_manager.py::rollback()` — restores files from manifest to original paths
- Called by `core/workflow_engine.py::_run_step()` after each successful `PATCH_GENERATOR` step

### Current Limitations
- Checkpoints are tied to workflow IDs, not task IDs
- Not called in the legacy tool loop (only in the workflow engine)
- On process restart, the workflow engine can resume from last step using `workflow.db`, but the legacy tool loop cannot

### Target (Phase 1)
- Every task checkpoint is linked to the task record in `tasks.db`
- On startup, any `RUNNING` task can be resumed from its last checkpoint
- The `Task` object tracks: `checkpoints: list[str]` (paths) + `current_step: int`
