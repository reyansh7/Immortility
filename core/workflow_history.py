"""Workflow event history for auditing and recovery."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.workflow_state import WorkflowStep


class WorkflowHistory:
    def __init__(self, db_path: str = "workflow.db") -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def log_event(
        self,
        workflow_id: str,
        event_type: str,
        step: WorkflowStep | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_events (workflow_id, event_type, step, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    event_type,
                    step.value if step else None,
                    json.dumps(payload or {}),
                    now,
                ),
            )
            conn.commit()

    def get_events(self, workflow_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, step, payload, created_at
                FROM workflow_events
                WHERE workflow_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (workflow_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
