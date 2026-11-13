"""IMMORTALITY Task model — the central unit of durable autonomous work.

Every multi-step operation is a Task with persistent state, checkpoints, and a
defined completion criterion.  Tasks survive process restarts.

Design constraints:
- All fields must be JSON-serialisable for SQLite storage.
- Dataclasses are frozen where immutability makes sense (sub-records).
- The Task itself is mutable through TaskStore methods only.
- No external imports beyond stdlib — this module must import cleanly at test time.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Helpers (defined first — used as dataclass field defaults) ─────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Enumerations ──────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    PAUSED = "paused"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"   # process crashed mid-task


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Sub-record types (frozen — updated by creating new instances) ─────────────

@dataclass(frozen=True)
class TaskStep:
    """One planned step inside a task."""
    index: int
    description: str
    expected_outcome: str
    status: str = StepStatus.PENDING.value
    started_at: str | None = None      # ISO-8601
    completed_at: str | None = None    # ISO-8601
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TaskStep":
        return TaskStep(
            index=int(d.get("index", 0)),
            description=str(d.get("description", "")),
            expected_outcome=str(d.get("expected_outcome", "")),
            status=str(d.get("status", StepStatus.PENDING.value)),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            error=d.get("error"),
        )

    def mark_running(self) -> "TaskStep":
        return TaskStep(
            index=self.index,
            description=self.description,
            expected_outcome=self.expected_outcome,
            status=StepStatus.RUNNING.value,
            started_at=_now_iso(),
            completed_at=self.completed_at,
            error=self.error,
        )

    def mark_completed(self) -> "TaskStep":
        return TaskStep(
            index=self.index,
            description=self.description,
            expected_outcome=self.expected_outcome,
            status=StepStatus.COMPLETED.value,
            started_at=self.started_at,
            completed_at=_now_iso(),
            error=None,
        )

    def mark_failed(self, error: str) -> "TaskStep":
        return TaskStep(
            index=self.index,
            description=self.description,
            expected_outcome=self.expected_outcome,
            status=StepStatus.FAILED.value,
            started_at=self.started_at,
            completed_at=_now_iso(),
            error=error[:2000],
        )


@dataclass(frozen=True)
class Observation:
    """The result of a single tool call recorded against a task step."""
    step_index: int
    tool_name: str
    args_summary: str          # First 200 chars of JSON-encoded args (no secrets)
    result_summary: str        # First 500 chars of result
    success: bool
    timestamp: str             # ISO-8601

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Observation":
        return Observation(
            step_index=int(d.get("step_index", 0)),
            tool_name=str(d.get("tool_name", "")),
            args_summary=str(d.get("args_summary", "")),
            result_summary=str(d.get("result_summary", "")),
            success=bool(d.get("success", True)),
            timestamp=str(d.get("timestamp", _now_iso())),
        )

    @staticmethod
    def create(
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        success: bool,
    ) -> "Observation":
        import json
        try:
            args_str = json.dumps(args, default=str)[:200]
        except Exception:
            args_str = str(args)[:200]
        return Observation(
            step_index=step_index,
            tool_name=tool_name,
            args_summary=args_str,
            result_summary=str(result)[:500],
            success=success,
            timestamp=_now_iso(),
        )


@dataclass(frozen=True)
class ErrorRecord:
    """A recorded failure during task execution."""
    step_index: int
    error_type: str    # permission_denied | tool_failed | verification_failed | timeout | model_error
    message: str
    timestamp: str     # ISO-8601
    retry_number: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ErrorRecord":
        return ErrorRecord(
            step_index=int(d.get("step_index", 0)),
            error_type=str(d.get("error_type", "unknown")),
            message=str(d.get("message", ""))[:2000],
            timestamp=str(d.get("timestamp", _now_iso())),
            retry_number=int(d.get("retry_number", 0)),
        )

    @staticmethod
    def create(
        step_index: int,
        error_type: str,
        message: str,
        retry_number: int = 0,
    ) -> "ErrorRecord":
        return ErrorRecord(
            step_index=step_index,
            error_type=error_type,
            message=str(message)[:2000],
            timestamp=_now_iso(),
            retry_number=retry_number,
        )


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of the hierarchical verification gate."""
    passed: bool
    method: str       # diff_review | ci_gate | semantic_review | composite | mechanical
    details: str
    timestamp: str    # ISO-8601
    mechanical_passed: bool = True
    tests_passed: bool = True
    diff_passed: bool = True
    semantic_passed: bool = True
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "VerificationResult":
        return VerificationResult(
            passed=bool(d.get("passed", False)),
            method=str(d.get("method", "")),
            details=str(d.get("details", "")),
            timestamp=str(d.get("timestamp", _now_iso())),
            mechanical_passed=bool(d.get("mechanical_passed", True)),
            tests_passed=bool(d.get("tests_passed", True)),
            diff_passed=bool(d.get("diff_passed", True)),
            semantic_passed=bool(d.get("semantic_passed", True)),
            failures=list(d.get("failures") or []),
            warnings=list(d.get("warnings") or []),
        )

    @staticmethod
    def create(
        passed: bool,
        method: str,
        details: str,
        *,
        mechanical_passed: bool = True,
        tests_passed: bool = True,
        diff_passed: bool = True,
        semantic_passed: bool = True,
        failures: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> "VerificationResult":
        return VerificationResult(
            passed=passed,
            method=method,
            details=str(details)[:2000],
            timestamp=_now_iso(),
            mechanical_passed=mechanical_passed,
            tests_passed=tests_passed,
            diff_passed=diff_passed,
            semantic_passed=semantic_passed,
            failures=list(failures or []),
            warnings=list(warnings or []),
        )


# ── Core Task dataclass ───────────────────────────────────────────────────────

@dataclass
class Task:
    """Persistent unit of autonomous work.

    Mutated only through TaskStore methods to ensure SQLite writes are atomic.
    Direct field assignment is allowed during construction; after that use the
    TaskStore update methods.
    """

    # Identity
    id: str
    goal: str

    # Status
    status: str = TaskStatus.CREATED.value
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None

    # Plan
    plan: list[TaskStep] = field(default_factory=list)
    current_step: int = 0

    # Execution
    agent: str = "immortality"   # immortality | hermes | coding_engine | workflow_engine
    observations: list[Observation] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    retries: int = 0

    # Permissions
    permission_mode: str = "assisted"
    permission_grants: list[str] = field(default_factory=list)

    # Artifacts & recovery
    artifacts: list[str] = field(default_factory=list)
    checkpoints: list[str] = field(default_factory=list)

    # Verification
    verification: VerificationResult | None = None

    # Result
    final_result: str | None = None

    # Interactive state & metadata
    pending_action: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Construction ──────────────────────────────────────────────────────────

    @staticmethod
    def create(
        goal: str,
        *,
        agent: str = "immortality",
        permission_mode: str = "assisted",
        metadata: dict[str, Any] | None = None,
    ) -> "Task":
        """Create a fresh Task (not yet persisted)."""
        from core.permissions import get_mode
        try:
            mode = permission_mode or get_mode()
        except Exception:
            mode = "assisted"
        return Task(
            id=str(uuid.uuid4()),
            goal=str(goal)[:2000],
            status=TaskStatus.CREATED.value,
            agent=agent,
            permission_mode=mode,
            metadata=dict(metadata or {}),
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        import json
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "plan": json.dumps([s.to_dict() for s in self.plan]),
            "current_step": self.current_step,
            "agent": self.agent,
            "observations": json.dumps([o.to_dict() for o in self.observations]),
            "errors": json.dumps([e.to_dict() for e in self.errors]),
            "retries": self.retries,
            "permission_mode": self.permission_mode,
            "permission_grants": json.dumps(self.permission_grants),
            "artifacts": json.dumps(self.artifacts),
            "checkpoints": json.dumps(self.checkpoints),
            "verification": json.dumps(self.verification.to_dict()) if self.verification else None,
            "final_result": self.final_result,
            "pending_action": json.dumps(self.pending_action) if self.pending_action else None,
            "metadata": json.dumps(self.metadata),
        }

    @staticmethod
    def from_row(row: dict[str, Any]) -> "Task":
        import json

        def _load_list(key: str, loader):
            raw = row.get(key) or "[]"
            try:
                items = json.loads(raw)
                return [loader(i) for i in items]
            except Exception:
                return []

        verification = None
        raw_v = row.get("verification")
        if raw_v:
            try:
                verification = VerificationResult.from_dict(json.loads(raw_v))
            except Exception:
                pass

        pending_action = None
        raw_pa = row.get("pending_action")
        if raw_pa:
            try:
                pending_action = json.loads(raw_pa) if isinstance(raw_pa, str) else raw_pa
            except Exception:
                pass

        metadata = {}
        raw_meta = row.get("metadata")
        if raw_meta:
            try:
                metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            except Exception:
                pass

        return Task(
            id=str(row["id"]),
            goal=str(row.get("goal", "")),
            status=str(row.get("status", TaskStatus.CREATED.value)),
            created_at=str(row.get("created_at", _now_iso())),
            updated_at=str(row.get("updated_at", _now_iso())),
            completed_at=row.get("completed_at"),
            plan=_load_list("plan", TaskStep.from_dict),
            current_step=int(row.get("current_step", 0)),
            agent=str(row.get("agent", "immortality")),
            observations=_load_list("observations", Observation.from_dict),
            errors=_load_list("errors", ErrorRecord.from_dict),
            retries=int(row.get("retries", 0)),
            permission_mode=str(row.get("permission_mode", "assisted")),
            permission_grants=_load_list("permission_grants", str),
            artifacts=_load_list("artifacts", str),
            checkpoints=_load_list("checkpoints", str),
            verification=verification,
            final_result=row.get("final_result"),
            pending_action=pending_action,
            metadata=metadata,
        )

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status in {
            TaskStatus.CREATED.value,
            TaskStatus.PLANNING.value,
            TaskStatus.EXECUTING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.RECOVERING.value,
            TaskStatus.PAUSED.value,
            TaskStatus.WAITING_PERMISSION.value,
            TaskStatus.WAITING_INPUT.value,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }

    @property
    def is_interrupted(self) -> bool:
        return self.status == TaskStatus.INTERRUPTED.value

    @property
    def current_step_obj(self) -> TaskStep | None:
        if 0 <= self.current_step < len(self.plan):
            return self.plan[self.current_step]
        return None

    def summary(self) -> str:
        """One-line human-readable summary for CLI/HUD display."""
        goal_preview = self.goal[:60] + ("…" if len(self.goal) > 60 else "")
        steps = len(self.plan)
        step_info = f"step {self.current_step + 1}/{steps}" if steps else "no plan"
        return (
            f"[{self.id[:8]}] {self.status.upper()} | {step_info} | "
            f"{len(self.observations)} obs, {len(self.errors)} err | "
            f"{goal_preview}"
        )
