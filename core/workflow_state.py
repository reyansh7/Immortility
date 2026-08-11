"""Persistent workflow state with SQLite backing."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStep(str, Enum):
    PLANNING = "planning"
    RESEARCH = "research"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    EDIT_PLANNER = "edit_planner"
    PATCH_GENERATOR = "patch_generator"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    DEBUGGER = "debugger"
    REFLECTOR = "reflector"
    EXPERIENCE_UPDATE = "experience_update"


class WorkflowState:
    """SQLite-backed workflow state store with pause/resume support."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            try:
                from core.repo_paths import workflow_db_path

                db_path = workflow_db_path()
            except Exception:
                db_path = Path("workflow.db")
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    context TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_workflow(
        self, workflow_id: str, step: WorkflowStep, context: dict[str, Any]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO workflows (id, status, current_step, context, paused, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    workflow_id,
                    WorkflowStatus.PENDING.value,
                    step.value,
                    json.dumps(context),
                    now,
                    now,
                ),
            )
            conn.commit()

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "status": WorkflowStatus(row["status"]),
            "current_step": WorkflowStep(row["current_step"]),
            "context": json.loads(row["context"]),
            "paused": bool(row["paused"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_workflow(
        self,
        workflow_id: str,
        *,
        status: WorkflowStatus | None = None,
        current_step: WorkflowStep | None = None,
        context: dict[str, Any] | None = None,
        paused: bool | None = None,
    ) -> None:
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            return
        if status is not None:
            workflow["status"] = status
        if current_step is not None:
            workflow["current_step"] = current_step
        if context is not None:
            workflow["context"] = context
        if paused is not None:
            workflow["paused"] = paused
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE workflows
                SET status = ?, current_step = ?, context = ?, paused = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    workflow["status"].value
                    if isinstance(workflow["status"], WorkflowStatus)
                    else workflow["status"],
                    workflow["current_step"].value
                    if isinstance(workflow["current_step"], WorkflowStep)
                    else workflow["current_step"],
                    json.dumps(workflow["context"]),
                    1 if workflow.get("paused") else 0,
                    now,
                    workflow_id,
                ),
            )
            conn.commit()

    def pause_workflow(self, workflow_id: str) -> None:
        self.update_workflow(workflow_id, status=WorkflowStatus.PAUSED, paused=True)

    def resume_workflow(self, workflow_id: str) -> None:
        self.update_workflow(workflow_id, status=WorkflowStatus.RUNNING, paused=False)

    def list_workflows(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, status, current_step, updated_at FROM workflows "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
