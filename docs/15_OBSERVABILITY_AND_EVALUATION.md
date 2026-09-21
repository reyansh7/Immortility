# 15 — Observability and Evaluation

## Why Observability Matters

Autonomous execution without observability is dangerous and undebuggable. Every action Immortality takes autonomously must be:
1. **Traceable:** We can reconstruct exactly what happened
2. **Attributable:** We know which model call or tool produced each decision
3. **Measurable:** We can quantify whether the system is improving

---

## Current Observability (Implemented)

### `core/harness.py` — Trace Events

Every model call, tool call, and task emits a `TraceEvent`:

```python
@dataclass
class TraceEvent:
    kind: str              # "model" | "tool" | "task" | "coding" | "command"
    execution_id: str      # UUID per user turn
    task_id: str           # task it belongs to
    mode: str              # FAST | AGENT | BACKGROUND
    model: str             # model id used
    tool: str              # tool name (if tool call)
    ts: float              # Unix timestamp
    ttft_ms: int | None    # time to first token
    total_ms: int | None   # total latency
    tokens_in: int | None
    tokens_out: int | None
    error: str             # empty if success
    retries: int
    cancelled: bool
    vram_mb: float | None  # GPU memory at time of call
    ram_mb: float | None   # RAM at time of call
    detail: str            # context-specific detail
```

Written to: `logs/immortility_events.jsonl` (rotating, max 5MB)

### HUD Vitals (`tools/hud_vitals.py`)

Sampled continuously when HUD is open:
- CPU utilization (%)
- RAM used/total (MB)
- GPU utilization (%)
- VRAM used/total (MB)
- LLM latency (last RTT in ms)

### Event Log (`logs/immortility_events.jsonl`)

Every classified action writes a JSON line:
```json
{
    "timestamp": "2026-09-21T09:45:00Z",
    "mode": "AGENT",
    "query": "run the tests",
    "retrieved": "...",
    "verification_passed": true,
    "failure_reason": ""
}
```

---

## What Is Missing

1. **Per-task trace timeline** — no UI shows "step 1: git_status (12ms) → step 2: read_file (8ms) → ..."
2. **Task success metrics** — no dashboard showing success rates over time
3. **Latency breakdown** — TTFT vs total time vs tool time vs LLM time
4. **Token cost tracking** — no accumulation of tokens used per task
5. **Retry analysis** — how often does the system need to retry, and why?

---

## Target Observability Architecture

### Task Timeline (HUD)

Each task should be visualizable as:

```
Task #42: "Fix the failing authentication tests"
Status: COMPLETED ✅ (3m 24s)

Step 0 [PLANNING]       - 12s  ✓ Generated 5-step plan
Step 1 [git_status]     - 0.3s ✓ 2 modified files
Step 2 [read_file]      - 0.1s ✓ core/auth.py (182 lines)
Step 3 [read_file]      - 0.1s ✓ tests/test_auth.py (94 lines)
Step 4 [edit_file]      - 0.2s ✓ Fixed import path
Step 5 [run_command]    - 18s  ✓ pytest: 5 passed
Step 6 [VERIFICATION]   - 8s   ✓ Diff approved, CI passed
Step 7 [DONE]           - 0.1s ✓ "Fixed auth import. All tests pass."

Model: kimi-k3 | Tokens: 2,847 in, 412 out | Total: 38s
```

### Structured Metrics

```python
# Collected per task:
@dataclass
class TaskMetrics:
    task_id: str
    goal: str
    status: TaskStatus
    total_duration_s: float
    step_count: int
    tool_calls: int
    model_calls: int
    retries: int
    tokens_in: int
    tokens_out: int
    verification_passed: bool
    agent: str
    model_id: str
```

---

## Evaluation Strategy

### What We Are Measuring

Immortality is not a chatbot. We do not measure BLEU scores or semantic similarity. We measure **task success**.

**Definition of success:** A task is successful if:
1. The task is marked `COMPLETED`
2. The verification gate passed (if the task involved editing)
3. The user did not manually undo the result

### Benchmark Task Suite (Phase 9)

A set of reproducible tasks that can be run against the system:

**Tier 1 — Coding tasks (automated verification)**
```
1. "Fix the ImportError in tests/test_hermes_backend.py"
   → success: pytest tests/test_hermes_backend.py passes
   
2. "Add a docstring to the KnowledgeEngine class"
   → success: docstring present in knowledge/engine.py, py_compile passes
   
3. "Find all functions that call chat() and list them"
   → success: output matches grep result, no invented functions
   
4. "Run the test suite and report which tests fail"
   → success: output matches actual pytest output
```

**Tier 2 — Information retrieval tasks (deterministic ground truth)**
```
5. "What Python version does this project use?"
   → success: answer mentions 3.11 or 3.13 (from .python-version or venv)
   
6. "List all the git tools available"
   → success: output includes all 22 git_* tools, no invented ones
   
7. "What does the verification gate check?"
   → success: mentions diff review, semantic reviewer, CI gate
```

**Tier 3 — System tasks (observable outcomes)**
```
8. "Open the Immortality project and index it"
   → success: knowledge/engine reports chunks indexed > 0
   
9. "Take a screenshot" (Phase 3+)
   → success: PNG file written to expected path
   
10. "Search the web for 'Python asyncio best practices' and summarize"
    → success: output contains relevant points, sources cited
```

### Measurement

```python
# Run benchmarks:
python -m tests.benchmark_suite --tier 1 --output results.json

# Results format:
{
    "run_id": "benchmark_2026-09-21",
    "total": 10,
    "passed": 8,
    "failed": 2,
    "pass_rate": 0.80,
    "avg_duration_s": 24.3,
    "avg_retries": 0.4,
    "tier1_pass_rate": 0.75,
    "tier2_pass_rate": 1.00,
    "tier3_pass_rate": 0.50,
    "tasks": [...]
}
```

### Regression Testing

Before any significant change:
1. Run the benchmark suite
2. Record results as baseline
3. Make the change
4. Re-run benchmark suite
5. If pass_rate drops by > 5%, investigate before merging

This gives a quantitative measure of whether a change improved or regressed the system.

---

## HUD Observability Panel (Phase 9)

The HUD should display:

### Active Task View
- Current task goal
- Progress bar through steps
- Current tool executing
- Tool call latency (live)
- Model calls counter
- Retry counter

### Task History
- List of last 20 tasks
- Status (completed/failed/cancelled)
- Duration
- Retry count
- Click to expand full timeline

### System Metrics
- Already implemented via `hud_vitals.py`
- Add: LLM calls per hour, tool calls per hour, task success rate (7-day rolling)

### Permissions Log
- List of last 10 permission decisions
- Each shows: tool, args summary, decision (allow/confirm/deny), outcome

---

## Logging Strategy

### Current Logs
- `logs/immortility.log` — general application log (rotating, 5MB max)
- `logs/immortility_events.jsonl` — structured execution traces
- `logs/diffs/` — unified diffs from code edits

### Target Logs (Phase 9)
- `logs/audit.jsonl` — HIGH-risk action audit log (append-only)
- `logs/tasks.jsonl` — completed task summaries
- `logs/benchmarks/` — benchmark run results

### Log Level Policy
- `DEBUG`: detailed internal state (disabled in production)
- `INFO`: task creation, step completion, model calls, tool results
- `WARNING`: fallback model used, rerank disabled, VRAM pressure
- `ERROR`: tool failures, permission denials, unexpected exceptions
