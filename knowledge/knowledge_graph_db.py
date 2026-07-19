"""SQLite Knowledge Graph + hierarchical / project / reflection memory."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


DEFAULT_DB = ".immortility/knowledge_graph.db"


class KnowledgeGraphDB:
    """Persistent structural intelligence store (no vector / LLM dependency)."""

    def __init__(self, db_path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    UNIQUE(project, path)
                );

                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    start_line INTEGER NOT NULL DEFAULT 0,
                    end_line INTEGER NOT NULL DEFAULT 0,
                    parent_symbol TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

                CREATE TABLE IF NOT EXISTS dependencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    imported_name TEXT NOT NULL,
                    source_module TEXT NOT NULL DEFAULT '',
                    raw TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_deps_module ON dependencies(source_module);

                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    caller TEXT NOT NULL,
                    callee TEXT NOT NULL,
                    line INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee);
                CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller);

                CREATE TABLE IF NOT EXISTS file_summaries (
                    file_id INTEGER PRIMARY KEY,
                    purpose TEXT NOT NULL DEFAULT '',
                    exports_json TEXT NOT NULL DEFAULT '[]',
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS folder_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    UNIQUE(project, folder)
                );

                CREATE TABLE IF NOT EXISTS repo_summaries (
                    project TEXT PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(project, fact_key)
                );

                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL DEFAULT '',
                    task TEXT NOT NULL,
                    what_broke TEXT NOT NULL DEFAULT '',
                    what_fixed_it TEXT NOT NULL DEFAULT '',
                    files_modified_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL
                );
                """
            )
            conn.commit()

    # ── File / graph writes ─────────────────────────────────────────

    def upsert_file_graph(
        self,
        project: str,
        path: str,
        language: str,
        sha256: str,
        symbols: list[dict[str, Any]],
        dependencies: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        summary: dict[str, Any] | None = None,
    ) -> int:
        """Replace structural data for one file. Returns file_id."""
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (project, path, language, sha256, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project, path) DO UPDATE SET
                    language=excluded.language,
                    sha256=excluded.sha256,
                    updated_at=excluded.updated_at
                """,
                (project, path, language, sha256, now),
            )
            row = conn.execute(
                "SELECT id FROM files WHERE project = ? AND path = ?",
                (project, path),
            ).fetchone()
            file_id = int(row["id"])

            conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM dependencies WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM calls WHERE file_id = ?", (file_id,))

            for s in symbols:
                conn.execute(
                    """
                    INSERT INTO symbols (file_id, name, kind, start_line, end_line, parent_symbol)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        s.get("name", ""),
                        s.get("kind", "unknown"),
                        int(s.get("start_line", 0) or 0),
                        int(s.get("end_line", 0) or 0),
                        s.get("parent_symbol", "") or "",
                    ),
                )
            for d in dependencies:
                conn.execute(
                    """
                    INSERT INTO dependencies (file_id, imported_name, source_module, raw)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        d.get("imported_name", "") or "",
                        d.get("source_module", "") or "",
                        d.get("raw", "") or "",
                    ),
                )
            for c in calls:
                conn.execute(
                    """
                    INSERT INTO calls (file_id, caller, callee, line)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        c.get("caller", "") or "",
                        c.get("callee", "") or "",
                        int(c.get("line", 0) or 0),
                    ),
                )

            if summary is not None:
                conn.execute(
                    """
                    INSERT INTO file_summaries (file_id, purpose, exports_json, dependencies_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        purpose=excluded.purpose,
                        exports_json=excluded.exports_json,
                        dependencies_json=excluded.dependencies_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        file_id,
                        summary.get("purpose", "") or "",
                        json.dumps(summary.get("exports", [])),
                        json.dumps(summary.get("dependencies", [])),
                        now,
                    ),
                )
            conn.commit()
            return file_id

    def remove_file(self, project: str, path: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM files WHERE project = ? AND path = ?",
                (project, path),
            )
            conn.commit()

    # ── Queries (no vector / LLM) ───────────────────────────────────

    def lookup_symbol(self, name: str, project: str | None = None) -> list[dict[str, Any]]:
        """Find defining files + imports for a symbol name."""
        sql = """
            SELECT s.name AS symbol, s.kind, s.start_line, s.end_line, s.parent_symbol,
                   f.path AS file_path, f.project, f.language
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.name = ?
        """
        params: list[Any] = [name]
        if project:
            sql += " AND f.project = ?"
            params.append(project)
        sql += " ORDER BY f.path"

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            results: list[dict[str, Any]] = []
            for r in rows:
                deps = conn.execute(
                    """
                    SELECT imported_name, source_module, raw
                    FROM dependencies WHERE file_id = (
                        SELECT id FROM files WHERE project = ? AND path = ?
                    )
                    """,
                    (r["project"], r["file_path"]),
                ).fetchall()
                callees = conn.execute(
                    """
                    SELECT DISTINCT callee FROM calls
                    WHERE file_id = (SELECT id FROM files WHERE project = ? AND path = ?)
                      AND caller = ?
                    """,
                    (r["project"], r["file_path"], name),
                ).fetchall()
                callers = conn.execute(
                    """
                    SELECT DISTINCT f2.path AS caller_file, c.caller
                    FROM calls c
                    JOIN files f2 ON f2.id = c.file_id
                    WHERE c.callee = ?
                    """,
                    (name,),
                ).fetchall()
                results.append(
                    {
                        "symbol": r["symbol"],
                        "kind": r["kind"],
                        "file_path": r["file_path"],
                        "project": r["project"],
                        "language": r["language"],
                        "start_line": r["start_line"],
                        "end_line": r["end_line"],
                        "parent_symbol": r["parent_symbol"],
                        "dependencies": [dict(d) for d in deps],
                        "callees": [c["callee"] for c in callees],
                        "callers": [dict(c) for c in callers],
                    }
                )
        return results

    # ── Hierarchical summaries ──────────────────────────────────────

    def list_file_summaries(self, project: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.path, fs.purpose, fs.exports_json, fs.dependencies_json
                FROM file_summaries fs
                JOIN files f ON f.id = fs.file_id
                WHERE f.project = ?
                ORDER BY f.path
                """,
                (project,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "path": r["path"],
                    "purpose": r["purpose"],
                    "exports": json.loads(r["exports_json"] or "[]"),
                    "dependencies": json.loads(r["dependencies_json"] or "[]"),
                }
            )
        return out

    def upsert_folder_summary(self, project: str, folder: str, summary: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO folder_summaries (project, folder, summary, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project, folder) DO UPDATE SET
                    summary=excluded.summary, updated_at=excluded.updated_at
                """,
                (project, folder, summary, time.time()),
            )
            conn.commit()

    def upsert_repo_summary(self, project: str, summary: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repo_summaries (project, summary, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(project) DO UPDATE SET
                    summary=excluded.summary, updated_at=excluded.updated_at
                """,
                (project, summary[:4000], time.time()),
            )
            conn.commit()

    def get_repo_summary(self, project: str) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT summary FROM repo_summaries WHERE project = ?",
                (project,),
            ).fetchone()
        return row["summary"] if row else ""

    def get_folder_summaries(self, project: str) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT folder, summary FROM folder_summaries WHERE project = ? ORDER BY folder",
                (project,),
            ).fetchall()
        return [{"folder": r["folder"], "summary": r["summary"]} for r in rows]

    # ── Project facts & reflections ─────────────────────────────────

    def set_fact(self, project: str, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_facts (project, fact_key, fact_value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project, fact_key) DO UPDATE SET
                    fact_value=excluded.fact_value, updated_at=excluded.updated_at
                """,
                (project, key, value, time.time()),
            )
            conn.commit()

    def list_facts(self, project: str) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT fact_key, fact_value FROM project_facts WHERE project = ? ORDER BY fact_key",
                (project,),
            ).fetchall()
        return [{"key": r["fact_key"], "value": r["fact_value"]} for r in rows]

    def add_reflection(
        self,
        task: str,
        what_broke: str,
        what_fixed_it: str,
        files_modified: list[str],
        project: str = "",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reflections
                (project, task, what_broke, what_fixed_it, files_modified_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project,
                    task,
                    what_broke,
                    what_fixed_it,
                    json.dumps(files_modified),
                    time.time(),
                ),
            )
            conn.commit()

    def list_reflections(self, project: str = "", limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if project:
                rows = conn.execute(
                    """
                    SELECT * FROM reflections WHERE project = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (project, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "project": r["project"],
                    "task": r["task"],
                    "what_broke": r["what_broke"],
                    "what_fixed_it": r["what_fixed_it"],
                    "files_modified": json.loads(r["files_modified_json"] or "[]"),
                    "created_at": r["created_at"],
                }
            )
        return out
