"""IMMORTALITY TaskManager — high-level task lifecycle.

Sits above TaskStore (raw persistence) and CheckpointManager (file snapshots).
This is the surface that action_engine.py and the harness call.

Key operations:
    create_task()       → creates + persists a new Task, returns it
    begin_step()        → marks a step as RUNNING
    complete_step()     → marks a step as COMPLETED
    record_observation()→ records a tool call result
    record_error()      → records a failure
    checkpoint_task()   → snapshots current modified files
    complete_task()     → marks COMPLETED + triggers reflection
    fail_task()         → marks FAILED
    cancel_task()       → marks CANCELLED
    resume_task()       → transitions INTERRUPTED → EXECUTING
    startup_recover()   → lists interrupted tasks for CLI prompt
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.task import (
    ErrorRecord,
    Observation,
    Task,
    TaskStatus,
    TaskStep,
    VerificationResult,
    _now_iso,
)
from core.task_db import TaskStore, get_task_store

logger = logging.getLogger(__name__)


class TaskManager:
    """High-level task lifecycle coordinator.

    Instantiate fresh or share the global singleton.
    """

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store or get_task_store()

    @property
    def store(self) -> TaskStore:
        return self._store

    # ── Creation ──────────────────────────────────────────────────────────────

    def create_task(
        self,
        goal: str,
        *,
        agent: str = "immortality",
        permission_mode: str | None = None,
    ) -> Task:
        """Create, persist, and return a new Task."""
        task = Task.create(goal=goal, agent=agent, permission_mode=permission_mode or "assisted")
        self._store.create(task)
        logger.info("Task created: %s | %s", task.id[:8], task.goal[:80])
        return task

    # ── Plan ──────────────────────────────────────────────────────────────────

    def set_plan(self, task_id: str, steps: list[tuple[str, str]]) -> None:
        """Assign a plan to a task.

        Each step is (description, expected_outcome).
        Transitions status to PLANNING.
        """
        plan = [
            TaskStep(
                index=i,
                description=desc,
                expected_outcome=outcome,
            )
            for i, (desc, outcome) in enumerate(steps)
        ]
        self._store.update_plan(task_id, plan)
        self._store.update_status(task_id, TaskStatus.PLANNING)
        logger.debug("Plan set: task=%s steps=%d", task_id[:8], len(plan))

    # ── Step lifecycle ────────────────────────────────────────────────────────

    def begin_step(self, task_id: str, step_index: int) -> None:
        task = self._store.get(task_id)
        if task is None:
            return
        updated_plan = []
        for s in task.plan:
            if s.index == step_index:
                updated_plan.append(s.mark_running())
            else:
                updated_plan.append(s)
        self._store.update_plan(task_id, updated_plan)
        self._store.update_current_step(task_id, step_index)
        self._store.update_status(task_id, TaskStatus.EXECUTING)

    def complete_step(self, task_id: str, step_index: int) -> None:
        task = self._store.get(task_id)
        if task is None:
            return
        updated_plan = []
        for s in task.plan:
            if s.index == step_index:
                updated_plan.append(s.mark_completed())
            else:
                updated_plan.append(s)
        self._store.update_plan(task_id, updated_plan)

    def fail_step(self, task_id: str, step_index: int, error: str) -> None:
        task = self._store.get(task_id)
        if task is None:
            return
        updated_plan = []
        for s in task.plan:
            if s.index == step_index:
                updated_plan.append(s.mark_failed(error))
            else:
                updated_plan.append(s)
        self._store.update_plan(task_id, updated_plan)

    # ── Observations & errors ─────────────────────────────────────────────────

    def record_observation(
        self,
        task_id: str,
        *,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
        success: bool,
    ) -> None:
        self._store.record_observation(
            task_id,
            step_index=step_index,
            tool_name=tool_name,
            args=args,
            result=result,
            success=success,
        )

    def record_error(
        self,
        task_id: str,
        *,
        step_index: int,
        error_type: str,
        message: str,
        retry_number: int = 0,
    ) -> None:
        self._store.record_error(
            task_id,
            step_index=step_index,
            error_type=error_type,
            message=message,
            retry_number=retry_number,
        )
        self._store.update_status(task_id, TaskStatus.RECOVERING)

    # ── Checkpointing ─────────────────────────────────────────────────────────

    def checkpoint_task(self, task_id: str, modified_files: list[str]) -> str | None:
        """Snapshot modified files and link the checkpoint to the task.

        Returns the checkpoint directory path, or None on failure.
        """
        if not modified_files:
            return None
        try:
            from core.checkpoint_manager import CheckpointManager

            mgr = CheckpointManager()
            task = self._store.get(task_id)
            context = {"task_id": task_id, "goal": task.goal if task else ""}
            ckpt_dir = mgr.save(task_id, context, modified_files)
            self._store.record_checkpoint(task_id, ckpt_dir)
            logger.debug("Checkpoint saved: task=%s dir=%s", task_id[:8], ckpt_dir)
            return ckpt_dir
        except Exception as exc:
            logger.warning("Checkpoint failed for task %s: %s", task_id[:8], exc)
            return None

    def rollback_task(self, task_id: str) -> list[str]:
        """Restore files from the most recent checkpoint.

        Returns list of restored file paths.
        """
        task = self._store.get(task_id)
        if task is None or not task.checkpoints:
            return []
        last_ckpt = task.checkpoints[-1]
        try:
            from core.checkpoint_manager import CheckpointManager

            mgr = CheckpointManager()
            restored = mgr.rollback(last_ckpt)
            logger.info(
                "Rollback: task=%s restored=%d files", task_id[:8], len(restored)
            )
            return restored
        except Exception as exc:
            logger.warning("Rollback failed for task %s: %s", task_id[:8], exc)
            return []

    # ── Terminal transitions ──────────────────────────────────────────────────

    def complete_task(self, task_id: str, final_result: str) -> None:
        self._store.complete(task_id, final_result)
        logger.info("Task completed: %s", task_id[:8])
        # Trigger async experience recording (best-effort, never blocks)
        self._try_reflect(task_id, success=True, message=final_result)

    def fail_task(self, task_id: str, reason: str) -> None:
        self._store.fail(task_id, reason)
        logger.info("Task failed: %s | %s", task_id[:8], reason[:80])
        self._try_reflect(task_id, success=False, message=reason)

    def cancel_task(self, task_id: str) -> None:
        self._store.cancel(task_id)
        logger.info("Task cancelled: %s", task_id[:8])

    def record_verification(self, task_id: str, result: VerificationResult) -> None:
        self._store.record_verification(task_id, result)
        status = TaskStatus.VERIFYING
        self._store.update_status(task_id, status)

    def record_artifact(self, task_id: str, path: str) -> None:
        self._store.record_artifact(task_id, path)

    # ── Resume / recovery ─────────────────────────────────────────────────────

    def resume_task(self, task_id: str) -> Task | None:
        """Transition an INTERRUPTED or PAUSED task back to EXECUTING."""
        task = self._store.get(task_id)
        if task is None:
            logger.warning("resume_task: task %s not found", task_id)
            return None
        if not (task.is_interrupted or task.status == TaskStatus.PAUSED.value):
            logger.warning(
                "resume_task: task %s is in status %s — nothing to resume",
                task_id[:8], task.status,
            )
            return task
        self._store.resume(task_id)
        logger.info("Task resumed: %s from step %d", task_id[:8], task.current_step)
        return self._store.get(task_id)

    def mark_interrupted(self, task_id: str) -> None:
        """Call on process shutdown for any active tasks."""
        self._store.mark_interrupted(task_id)

    def startup_recover(self) -> list[Task]:
        """Return INTERRUPTED tasks for display at startup."""
        tasks = self._store.list_interrupted()
        if tasks:
            logger.info(
                "Startup recovery: %d interrupted task(s) found", len(tasks)
            )
        return tasks

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Task | None:
        return self._store.get(task_id)

    def list_active(self) -> list[Task]:
        return self._store.list_active()

    def list_recent(self, limit: int = 20) -> list[Task]:
        return self._store.list_recent(limit)

    def _try_reflect(self, task_id: str, *, success: bool, message: str) -> None:
        """Best-effort post-task reflection; never raises."""
        try:
            task = self._store.get(task_id)
            if task is None:
                return
            from core.self_reflection import reflect_and_store

            reflect_and_store(
                task=task.goal,
                tools_used=[
                    o.tool_name
                    for o in task.observations
                    if o.tool_name
                ],
                result=message,
                error="" if success else message,
                solution=message if success else "",
                success=success,
                confidence=0.8 if success else 0.6,
            )
        except Exception as exc:
            logger.debug("Post-task reflection skipped: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_manager: TaskManager | None = None
_manager_lock = __import__("threading").Lock()


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = TaskManager()
    return _manager


def reset_task_manager() -> None:
    """Drop the singleton — for tests only."""
    global _manager
    with _manager_lock:
        _manager = None
