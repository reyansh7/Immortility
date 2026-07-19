"""SQLite-backed index state for SHA-256 incremental indexing."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileIndexEntry:
    filepath: str
    sha256: str
    last_indexed: float
    chunk_count: int
    chunk_ids: list[str] = field(default_factory=list)


class IndexStateDB:
    """Persistent filepath → hash/timestamp mapping in SQLite."""

    def __init__(self, db_path: str | Path = ".vector_db/index_state.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._maybe_migrate_json()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_hashes (
                    filepath TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    last_indexed REAL NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    chunk_ids TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.commit()

    def _maybe_migrate_json(self) -> None:
        """One-time import from legacy index_state.json next to the DB."""
        legacy = self._path.parent / "index_state.json"
        if not legacy.exists():
            return
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM file_hashes").fetchone()["c"]
            if count > 0:
                return
            try:
                raw = json.loads(legacy.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            for fp, data in raw.items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO file_hashes
                    (filepath, sha256, last_indexed, chunk_count, chunk_ids)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        data.get("sha256", ""),
                        float(data.get("last_indexed", time.time())),
                        int(data.get("chunk_count", 0)),
                        json.dumps(data.get("chunk_ids", [])),
                    ),
                )
            conn.commit()

    def get(self, filepath: str) -> FileIndexEntry | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM file_hashes WHERE filepath = ?", (filepath,)
            ).fetchone()
        if not row:
            return None
        try:
            chunk_ids = json.loads(row["chunk_ids"] or "[]")
        except json.JSONDecodeError:
            chunk_ids = []
        return FileIndexEntry(
            filepath=row["filepath"],
            sha256=row["sha256"],
            last_indexed=row["last_indexed"],
            chunk_count=row["chunk_count"],
            chunk_ids=chunk_ids,
        )

    def set(self, entry: FileIndexEntry) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO file_hashes
                (filepath, sha256, last_indexed, chunk_count, chunk_ids)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.filepath,
                    entry.sha256,
                    entry.last_indexed,
                    entry.chunk_count,
                    json.dumps(entry.chunk_ids),
                ),
            )
            conn.commit()

    def remove(self, filepath: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM file_hashes WHERE filepath = ?", (filepath,))
            conn.commit()

    def all_files(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT filepath FROM file_hashes").fetchall()
        return [r["filepath"] for r in rows]

    def save(self) -> None:
        """No-op: writes are immediate. Kept for Indexer API compatibility."""
        return None
