"""IMMORTALITY TaskStore — SQLite-backed durable task persistence.

All writes are atomic (single-row UPDATE inside a transaction).
Thread-safe: a single connection + threading.Lock per instance.

Usage::

    store = TaskStore()
    task = Task.create(goal="Fix the failing tests")
    store.create(task)
    store.update_status(task.id, TaskStatus.EXECUTING)
    store.record_observation(task.id, step_index=0,
                             tool_name="git_status", args={}, result="...", success=True)
    store.complete(task.id, final_result="All tests pass.")
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
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

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    goal             TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    agent            TEXT    NOT NULL  DEFAULT 'immortality',
    plan             TEXT    NOT NULL  DEFAULT '[]',
    current_step     INTEGER NOT NULL  DEFAULT 0,
    observations     TEXT    NOT NULL  DEFAULT '[]',
    errors           TEXT    NOT NULL  DEFAULT '[]',
    retries          INTEGER NOT NULL  DEFAULT 0,
    permission_mode  TEXT    NOT NULL  DEFAULT 'assisted',
    permission_grants TEXT   NOT NULL  DEFAULT '[]',
    artifacts        TEXT    NOT NULL  DEFAULT '[]',
    checkpoints      TEXT    NOT NULL  DEFAULT '[]',
    verification     TEXT             DEFAULT NULL,
    final_result     TEXT             DEFAULT NULL,
    pending_action   TEXT             DEFAULT NULL,
    metadata         TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    completed_at     TEXT             DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created   ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_updated   ON tasks(updated_at);
"""


class TaskStore:
    """Process-wide SQLite task store.  Uses a single connection with WAL mode."""

    _instance: "TaskStore | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            try:
                from core.repo_paths import tasks_db_path
                db_path = tasks_db_path()
            except Exception:
                db_path = Path("tasks.db")
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Singleton factory ─────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "TaskStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = TaskStore()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the singleton — for tests only."""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance._close()
                except Exception:
                    pass
            cls._instance = None

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(_SCHEMA)
            # Safe migrations for existing databases
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN pending_action TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN metadata TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        logger.debug("TaskStore initialised at %s", self._db_path)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(self, task: Task) -> None:
        """Persist a newly created Task.  Raises if id already exists."""
        row = task.to_dict()
        # to_dict() serialises plan/observations/errors/etc. as JSON strings
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_str = ", ".join(cols)
        values = [row[c] for c in cols]
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO tasks ({col_str}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()
            except sqlite3.IntegrityError:
                logger.warning("Task %s already exists in store", task.id)

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return Task.from_row(dict(row))

    def list_by_status(self, *statuses: str | TaskStatus) -> list[Task]:
        """Return tasks matching any of the given statuses, newest first."""
        vals = [s.value if isinstance(s, TaskStatus) else str(s) for s in statuses]
        if not vals:
            return []
        placeholders = ", ".join("?" for _ in vals)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                "ORDER BY updated_at DESC",
                vals,
            ).fetchall()
        return [Task.from_row(dict(r)) for r in rows]

    def list_active(self) -> list[Task]:
        """Tasks that are not yet in a terminal or interrupted state."""
        return self.list_by_status(
            TaskStatus.CREATED,
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.VERIFYING,
            TaskStatus.RECOVERING,
            TaskStatus.PAUSED,
        )

    def list_interrupted(self) -> list[Task]:
        """Tasks whose process crashed before they finished."""
        return self.list_by_status(TaskStatus.INTERRUPTED)

    def list_recent(self, limit: int = 20) -> list[Task]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Task.from_row(dict(r)) for r in rows]

    # ── Atomic update primitives ──────────────────────────────────────────────

    def _update_fields(self, task_id: str, **kwargs: Any) -> None:
        """Set arbitrary columns + updated_at atomically."""
        kwargs["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self._lock:
            conn = self._connect()
            conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()

    def update_status(self, task_id: str, status: TaskStatus | str) -> None:
        val = status.value if isinstance(status, TaskStatus) else str(status)
        extra: dict[str, Any] = {}
        if val in {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            extra["completed_at"] = _now_iso()
        self._update_fields(task_id, status=val, **extra)

    def update_plan(self, task_id: str, plan: list[TaskStep]) -> None:
        self._update_fields(
            task_id,
            plan=json.dumps([s.to_dict() for s in plan]),
        )

    def update_current_step(self, task_id: str, step: int) -> None:
        self._update_fields(task_id, current_step=step)

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
        obs = Observation.create(step_index, tool_name, args, result, success)
        task = self.get(task_id)
        if task is None:
            logger.warning("record_observation: task %s not found", task_id)
            return
        updated = list(task.observations) + [obs]
        self._update_fields(
            task_id,
            observations=json.dumps([o.to_dict() for o in updated]),
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
        err = ErrorRecord.create(step_index, error_type, message, retry_number)
        task = self.get(task_id)
        if task is None:
            return
        updated = list(task.errors) + [err]
        self._update_fields(
            task_id,
            errors=json.dumps([e.to_dict() for e in updated]),
            retries=task.retries + 1,
        )

    def record_artifact(self, task_id: str, path: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        artifacts = list(task.artifacts)
        if path not in artifacts:
            artifacts.append(path)
        self._update_fields(task_id, artifacts=json.dumps(artifacts))

    def record_checkpoint(self, task_id: str, checkpoint_dir: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        checkpoints = list(task.checkpoints) + [checkpoint_dir]
        self._update_fields(task_id, checkpoints=json.dumps(checkpoints))

    def record_verification(self, task_id: str, result: VerificationResult) -> None:
        self._update_fields(
            task_id,
            verification=json.dumps(result.to_dict()),
        )

    def complete(self, task_id: str, final_result: str) -> None:
        self._update_fields(
            task_id,
            status=TaskStatus.COMPLETED.value,
            final_result=str(final_result)[:10000],
            completed_at=_now_iso(),
        )

    def fail(self, task_id: str, reason: str) -> None:
        self._update_fields(
            task_id,
            status=TaskStatus.FAILED.value,
            final_result=str(reason)[:10000],
            completed_at=_now_iso(),
        )

    def mark_interrupted(self, task_id: str) -> None:
        """Called on SIGINT / process shutdown for running tasks."""
        self._update_fields(task_id, status=TaskStatus.INTERRUPTED.value)

    def cancel(self, task_id: str) -> None:
        self._update_fields(
            task_id,
            status=TaskStatus.CANCELLED.value,
            completed_at=_now_iso(),
        )

    def resume(self, task_id: str) -> None:
        """Transition an INTERRUPTED or PAUSED task back to EXECUTING."""
        self._update_fields(task_id, status=TaskStatus.EXECUTING.value)

    def update_pending_action(self, task_id: str, pending_action: dict[str, Any] | None) -> None:
        """Store or clear the interactive confirmation state."""
        raw = json.dumps(pending_action) if pending_action else None
        self._update_fields(task_id, pending_action=raw)


def get_task_store() -> TaskStore:
    """Process-wide singleton accessor."""
    return TaskStore.get_instance()
