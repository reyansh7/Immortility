# 11 — Autonomy and Tasks

## The Task Abstraction

The Task is the central unit of autonomous work. Every meaningful request that takes more than one tool call should be a Task — with persistent state, checkpoints, and a defined completion criterion.

The current system has working multi-step execution but no unified Task object. Tasks die with the process. This is the most critical architectural gap between Immortality today and JARVIS.

---

## Task Lifecycle

```
User request (voice or text)
    ↓
Intent classification → TASK
    ↓
Task.create(goal=request)
    → saves to tasks.db
    ↓
PLANNING
    → LLM generates plan: list of TaskSteps with expected outcomes
    → Task.update(plan=steps, status=PLANNING)
    ↓
EXECUTING
    → For each step:
        → permission check
        → tool execution
        → observation recorded
        → Task.record_observation(step, tool, result)
    ↓
VERIFYING
    → verification gate (diff + reviewer + CI)
    → Task.record_verification(result)
    ↓
COMPLETED
    → Task.complete(final_result)
    → experience_memory.record(task, outcome)
    → world_model.update(task_completed)
    ↓
REPORTED
    → response to user (text + voice if in voice mode)
```

### Failure Path
```
Any step fails
    ↓
errors.append(ErrorRecord)
retries += 1
    ↓
if retries < max_retries:
    → checkpoint.rollback() if files were modified
    → re-execute current step with debug context
else:
    → Task.status = FAILED
    → generate failure report
    → notify user
```

### Interruption Path
```
Process crash OR user cancels
    ↓
Task.status = INTERRUPTED (set atomically before crash if possible)
    ↓
On next startup:
    → query: tasks WHERE status IN ('running', 'interrupted')
    → display to user: "Task X was in progress. Resume?"
    → user confirms → Task.resume() from current_step
```

---

## Task Schema

```python
@dataclass
class Task:
    # Identity
    id: str                    # UUID, generated at creation
    goal: str                  # Original user request, verbatim
    
    # Status
    status: TaskStatus         # PLANNED|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED|INTERRUPTED
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    
    # Plan
    plan: list[TaskStep]       # Planned steps with expected outcomes
    current_step: int          # Index into plan (0-based)
    
    # Execution
    agent: str                 # "legacy_loop" | "coding_engine" | "hermes" | "workflow_engine"
    observations: list[Observation]  # Per-tool-call results
    errors: list[ErrorRecord]
    retries: int
    
    # Permissions
    permission_mode: str       # Mode at time of task creation
    permission_grants: list[str]  # Specific permissions that were granted
    
    # Artifacts
    artifacts: list[str]       # File paths created or modified
    checkpoints: list[str]     # Checkpoint directory paths (most recent last)
    
    # Verification
    verification: VerificationResult | None
    
    # Result
    final_result: str | None


@dataclass
class TaskStep:
    index: int
    description: str           # What this step does
    expected_outcome: str      # How we know it succeeded
    tool_calls: list[ToolCallRecord]
    status: StepStatus         # PENDING|RUNNING|COMPLETED|FAILED
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


@dataclass
class Observation:
    step_index: int
    tool_name: str
    args: dict
    result_summary: str        # First 500 chars of result
    success: bool
    timestamp: datetime


@dataclass
class ErrorRecord:
    step_index: int
    error_type: str            # permission_denied | tool_failed | verification_failed | timeout
    message: str
    timestamp: datetime
    retry_number: int


@dataclass
class VerificationResult:
    passed: bool
    method: str                # "diff_review" | "ci_gate" | "semantic_review"
    details: str
    timestamp: datetime
```

---

## Task Persistence (Phase 1)

### Storage: `tasks.db` (SQLite)

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    agent TEXT NOT NULL,
    plan TEXT DEFAULT '[]',          -- JSON: list[TaskStep]
    current_step INTEGER DEFAULT 0,
    observations TEXT DEFAULT '[]',  -- JSON: list[Observation]
    errors TEXT DEFAULT '[]',        -- JSON: list[ErrorRecord]
    retries INTEGER DEFAULT 0,
    permission_mode TEXT DEFAULT 'assisted',
    permission_grants TEXT DEFAULT '[]',
    artifacts TEXT DEFAULT '[]',     -- JSON: list[str]
    checkpoints TEXT DEFAULT '[]',   -- JSON: list[str]
    verification TEXT,               -- JSON: VerificationResult | null
    final_result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created ON tasks(created_at);
```

### Atomic Updates
Every task state change is a single SQLite `UPDATE` with a transaction. No partial writes.

```python
def update_task_status(task_id: str, status: TaskStatus) -> None:
    with db.transaction():
        db.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (status.value, now_iso(), task_id)
        )
```

---

## Startup Recovery

On every startup, `main.py` runs:

```python
async def startup_recovery():
    interrupted_tasks = task_db.query(
        "SELECT * FROM tasks WHERE status IN ('running', 'interrupted')"
    )
    for task in interrupted_tasks:
        print(f"\nTask interrupted: {task.goal[:80]}")
        print(f"  Step {task.current_step}/{len(task.plan)}, last updated {task.updated_at}")
        answer = input("Resume? [y/n/skip]: ").strip().lower()
        if answer == 'y':
            await resume_task(task)
        elif answer == 'n':
            task_db.update_status(task.id, TaskStatus.CANCELLED)
        # skip: leave as-is for later
```

For the HUD, this appears as a notification in the interface.

---

## Checkpoint Strategy

### When to Checkpoint
1. Before any file-modifying tool call
2. After each successful `TaskStep` completion
3. Before a risky command (destructive git, docker rm, etc.)

### Checkpoint Contents
```
.checkpoints/
    {task_id}_{timestamp}/
        context.json          ← task state at this point
        manifest.json         ← {saved_key: original_path}
        files/
            {saved_key}       ← copy of original file
```

### Rollback
If a step fails, the system can roll back to the most recent checkpoint:
1. Restore files from checkpoint `files/` directory using `manifest.json`
2. Set `task.current_step` to the step before the failed one
3. Retry the step with additional context about the failure

---

## Autonomy Modes and Task Behavior

| Permission Mode | Confirmation Required | Computer Control | Task Resume |
|---|---|---|---|
| SAFE | All mutating tools | None | Manual only |
| ASSISTED (default) | All mutating tools | screenshot OK, clicks need confirm | Prompted on startup |
| AUTONOMOUS | Destructive only | Most actions auto-approved | Auto-resume if flagged safe |
| DEVELOPER | Destructive only | All actions auto-approved | Auto-resume |

### AUTONOMOUS Mode Safety
Even in AUTONOMOUS mode:
- `git push --force` requires confirmation
- `rm -rf` style commands require confirmation
- Email/financial actions require confirmation
- First time a computer control action touches a new application requires confirmation

---

## Multi-Step Planning

For complex goals, the harness generates an explicit plan before starting execution:

```
Goal: "Set up a FastAPI backend with JWT authentication"
    ↓
Plan:
  Step 0: Create project directory structure
  Step 1: Initialize Python virtual environment
  Step 2: Create requirements.txt with FastAPI, uvicorn, PyJWT
  Step 3: Implement main.py with FastAPI app
  Step 4: Implement auth.py with JWT logic
  Step 5: Write tests/test_auth.py
  Step 6: Run tests and verify
  Step 7: Create README.md
```

Each step has:
- `description`: what to do
- `expected_outcome`: how to verify success
- `tool_calls`: to be populated during execution

The plan is stored in `tasks.db` before execution begins. If the process crashes after step 3, restart + resume executes from step 3 with full context.

---

## Long-Running Task Support

Some tasks run for minutes or hours:
- Indexing a large codebase
- Researching a complex topic
- Building and testing a full feature

These run as `BACKGROUND` tasks via `execution_kernel.run_task()`:
- Main UI remains responsive
- Progress updates visible in HUD
- User can inspect current step via `/task status {id}`
- User can cancel via `/task cancel {id}`
- Task persists through interruption

---

## Task Hierarchy

Tasks can have sub-tasks (for complex multi-phase work):

```
Parent Task: "Build complete authentication system"
    │
    ├── Sub-task: "Design the user schema"
    ├── Sub-task: "Implement JWT token generation"
    ├── Sub-task: "Write authentication middleware"
    ├── Sub-task: "Create login/logout endpoints"
    └── Sub-task: "Write integration tests"
```

In Phase 1, this is represented as a flat plan with phases. True hierarchical tasks are a Phase 6+ enhancement.

---

## Verification as a First-Class Concern

Every editing task must pass verification before being marked COMPLETED.

The verification gate (already implemented in `core/action_engine.py`):
1. **Diff check:** Generate unified diff of all modified files. If no diff, reject DONE.
2. **Semantic review:** Fresh LLM call (not coder context): "Does this diff satisfy the request?" → pass/fail with reason.
3. **CI gate:** Run allowlisted checks: `pytest {targets}`, `python -m py_compile {touched_files}`.

Verification failures trigger the recovery loop (up to `max_retries` attempts).

### DONE Conditions
A task is DONE when ALL of:
- Verification gate passed
- At least one mutating tool actually executed (for edit tasks)
- No unresolved errors in the observation list
- The semantic reviewer approved the diff

The agent loop cannot self-declare success by calling DONE with a fabricated message. The harness enforces this.
