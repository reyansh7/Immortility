"""Persistent TurboVec vector store for Immortility.

TurboQuant index (compressed vectors + uint64 ids) plus a SQLite sidecar
for documents/metadata. Public API matches the former Chroma wrapper so
KnowledgeEngine / hybrid search / indexer keep working unchanged.

On-disk layout (under persist_dir)::

    turbovec/<collection>.tvim   — IdMapIndex
    turbovec/<collection>.sqlite — string id, document, metadata JSON
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

CODE_COLLECTION = "code_chunks"
DOCS_COLLECTION = "documentation"
EMBEDDING_DIM = 384
BIT_WIDTH = 4


def _default_persist_dir() -> str:
    try:
        from core.repo_paths import vector_db_dir

        return str(vector_db_dir())
    except Exception:
        return ".vector_db"


DEFAULT_PERSIST_DIR = _default_persist_dir()


def _string_to_uid(chunk_id: str) -> int:
    """Stable id for TurboVec + SQLite (signed 63-bit — SQLite INTEGER max)."""
    digest = hashlib.blake2b(chunk_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little", signed=False) & 0x7FFFFFFFFFFFFFFF


class VectorStore:
    """Persistent local vector store backed by TurboVec + SQLite sidecar.

    Parameters:
        persist_dir: Root directory (``.vector_db``). Indexes live in a
            ``turbovec/`` subdirectory.
        collection_name: Logical collection (``code_chunks`` / ``documentation``).
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str = CODE_COLLECTION,
    ) -> None:
        self._persist_dir = persist_dir or _default_persist_dir()
        self._collection_name = collection_name
        self._root = Path(self._persist_dir) / "turbovec"
        self._index_path = self._root / f"{collection_name}.tvim"
        self._db_path = self._root / f"{collection_name}.sqlite"
        self._index: Any = None
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._dirty = False

    # ── Lazy connection ─────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if self._index is not None and self._conn is not None:
            return
        with self._lock:
            if self._index is not None and self._conn is not None:
                return
            self._root.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    uid INTEGER NOT NULL UNIQUE,
                    document TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    project TEXT,
                    filename TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_filename ON chunks(filename)"
            )
            self._conn.commit()

            from turbovec import IdMapIndex

            if self._index_path.is_file():
                try:
                    self._index = IdMapIndex.load(str(self._index_path))
                except Exception as exc:
                    logger.warning(
                        "TurboVec load failed (%s) — creating empty index", exc
                    )
                    self._index = IdMapIndex(dim=EMBEDDING_DIM, bit_width=BIT_WIDTH)
            else:
                self._index = IdMapIndex(dim=EMBEDDING_DIM, bit_width=BIT_WIDTH)

            logger.info(
                "TurboVec connected: dir=%s collection=%s chunks=%d",
                self._persist_dir,
                self._collection_name,
                self.count(),
            )

    def _persist_index(self) -> None:
        if self._index is None or not self._dirty:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        self._index.write(str(self._index_path))
        self._dirty = False

    # ── CRUD operations ─────────────────────────────────────────────

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Upsert chunks into the collection."""
        self._ensure_connected()
        if not ids:
            return
        assert self._index is not None and self._conn is not None

        with self._lock:
            # Remove existing vectors for upsert semantics
            existing_uids: list[int] = []
            for chunk_id in ids:
                row = self._conn.execute(
                    "SELECT uid FROM chunks WHERE id = ?", (chunk_id,)
                ).fetchone()
                if row:
                    existing_uids.append(int(row[0]))
            for uid in existing_uids:
                try:
                    self._index.remove(uid)
                except Exception as exc:
                    logger.debug("TurboVec remove %s: %s", uid, exc)

            batch_size = 500
            for i in range(0, len(ids), batch_size):
                end = i + batch_size
                batch_ids = ids[i:end]
                batch_emb = embeddings[i:end]
                batch_docs = documents[i:end]
                batch_meta = metadatas[i:end]

                uids = np.array(
                    [_string_to_uid(cid) for cid in batch_ids],
                    dtype=np.uint64,
                )
                vectors = np.asarray(batch_emb, dtype=np.float32)
                if vectors.ndim == 1:
                    vectors = vectors.reshape(1, -1)
                if vectors.shape[1] != EMBEDDING_DIM:
                    raise ValueError(
                        f"Expected embedding dim {EMBEDDING_DIM}, got {vectors.shape[1]}"
                    )
                self._index.add_with_ids(vectors, uids)

                for cid, uid, doc, meta in zip(
                    batch_ids, uids.tolist(), batch_docs, batch_meta, strict=True
                ):
                    meta = meta or {}
                    self._conn.execute(
                        """
                        INSERT INTO chunks (id, uid, document, metadata, project, filename)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            uid=excluded.uid,
                            document=excluded.document,
                            metadata=excluded.metadata,
                            project=excluded.project,
                            filename=excluded.filename
                        """,
                        (
                            cid,
                            int(uid),
                            doc,
                            json.dumps(meta, ensure_ascii=False),
                            str(meta.get("project") or ""),
                            str(meta.get("filename") or ""),
                        ),
                    )
            self._conn.commit()
            self._dirty = True
            self._persist_index()
            logger.debug("Upserted %d chunks (TurboVec).", len(ids))

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search by embedding similarity.

        Returns list of dicts: id, document, metadata, distance.
        ``distance`` is ``1 - score`` so hybrid search can keep
        ``similarity = 1 - distance`` (TurboVec scores are higher-is-better).
        """
        self._ensure_connected()
        assert self._index is not None and self._conn is not None

        total = self.count()
        if total == 0 or n_results <= 0:
            return []

        allowlist: np.ndarray | None = None
        if where:
            allowlist = self._uids_for_where(where)
            if allowlist is not None and allowlist.size == 0:
                return []

        query = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        k = min(int(n_results), total if allowlist is None else int(allowlist.size))
        if k <= 0:
            return []

        try:
            with self._lock:
                if allowlist is not None:
                    scores, found_uids = self._index.search(
                        query, k=k, allowlist=allowlist
                    )
                else:
                    scores, found_uids = self._index.search(query, k=k)
        except TypeError:
            # Older turbovec without allowlist kwarg
            with self._lock:
                scores, found_uids = self._index.search(query, k=k)
            if allowlist is not None:
                allowed = set(int(x) for x in allowlist.tolist())
                keep_s: list[float] = []
                keep_u: list[int] = []
                flat_s = np.asarray(scores).reshape(-1)
                flat_u = np.asarray(found_uids).reshape(-1)
                for s, u in zip(flat_s.tolist(), flat_u.tolist(), strict=False):
                    if int(u) in allowed:
                        keep_s.append(float(s))
                        keep_u.append(int(u))
                scores, found_uids = keep_s[:k], keep_u[:k]

        flat_scores = np.asarray(scores, dtype=np.float64).reshape(-1)
        flat_uids = np.asarray(found_uids, dtype=np.uint64).reshape(-1)

        results: list[dict[str, Any]] = []
        for score, uid in zip(flat_scores.tolist(), flat_uids.tolist(), strict=False):
            row = self._conn.execute(
                "SELECT id, document, metadata FROM chunks WHERE uid = ?",
                (int(uid),),
            ).fetchone()
            if not row:
                continue
            meta = {}
            try:
                meta = json.loads(row[2] or "{}")
            except json.JSONDecodeError:
                meta = {}
            # Map higher-is-better score → Chroma-style distance
            distance = float(max(0.0, 1.0 - float(score)))
            results.append(
                {
                    "id": row[0],
                    "document": row[1] or "",
                    "metadata": meta,
                    "distance": distance,
                    "score": float(score),
                }
            )
        return results

    def _uids_for_where(self, where: dict[str, Any]) -> np.ndarray:
        """Resolve metadata filter to TurboVec allowlist of uint64 ids."""
        assert self._conn is not None
        clauses: list[str] = []
        params: list[Any] = []
        # Support simple equality filters used by Immortility today
        for key, value in where.items():
            if key in {"project", "filename"}:
                clauses.append(f"{key} = ?")
                params.append(str(value))
            elif key == "$and" and isinstance(value, list):
                for part in value:
                    if isinstance(part, dict):
                        for k, v in part.items():
                            if k in {"project", "filename"}:
                                clauses.append(f"{k} = ?")
                                params.append(str(v))
        if not clauses:
            # Unknown filter → empty allowlist (safer than unfiltered)
            logger.debug("Unsupported where filter %s — empty allowlist", where)
            return np.array([], dtype=np.uint64)

        sql = "SELECT uid FROM chunks WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(sql, params).fetchall()
        return np.array([int(r[0]) for r in rows], dtype=np.uint64)

    def delete_by_file(self, filepath: str) -> int:
        """Remove all chunks belonging to a specific file."""
        return self._delete_where(filename=filepath)

    def delete_by_project(self, project: str) -> int:
        """Remove all chunks belonging to a project."""
        return self._delete_where(project=project)

    def _delete_where(self, *, filename: str | None = None, project: str | None = None) -> int:
        self._ensure_connected()
        assert self._index is not None and self._conn is not None
        with self._lock:
            if filename is not None:
                rows = self._conn.execute(
                    "SELECT id, uid FROM chunks WHERE filename = ?", (filename,)
                ).fetchall()
            elif project is not None:
                rows = self._conn.execute(
                    "SELECT id, uid FROM chunks WHERE project = ?", (project,)
                ).fetchall()
            else:
                return 0
            if not rows:
                return 0
            for _cid, uid in rows:
                try:
                    self._index.remove(int(uid))
                except Exception as exc:
                    logger.debug("TurboVec remove during delete: %s", exc)
            ids = [r[0] for r in rows]
            self._conn.executemany(
                "DELETE FROM chunks WHERE id = ?",
                [(i,) for i in ids],
            )
            self._conn.commit()
            self._dirty = True
            self._persist_index()
            logger.debug("Deleted %d chunks", len(ids))
            return len(ids)

    def get_all_documents(self) -> list[dict[str, Any]]:
        """Return all documents in the collection (for BM25 index building)."""
        self._ensure_connected()
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, document, metadata FROM chunks"
        ).fetchall()
        results: list[dict[str, Any]] = []
        for doc_id, document, meta_raw in rows:
            meta: dict[str, Any] = {}
            try:
                meta = json.loads(meta_raw or "{}")
            except json.JSONDecodeError:
                meta = {}
            results.append(
                {
                    "id": doc_id,
                    "document": document or "",
                    "metadata": meta,
                }
            )
        return results

    # ── Stats ───────────────────────────────────────────────────────

    def count(self) -> int:
        """Return the number of chunks in the collection."""
        self._ensure_connected()
        assert self._conn is not None
        row = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return int(row[0]) if row else 0

    def count_by_file(self, filename: str) -> int:
        """How many chunks are tagged with this absolute filepath."""
        self._ensure_connected()
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE filename = ?",
            (str(filename),),
        ).fetchone()
        return int(row[0]) if row else 0

    def count_by_project(self, project: str) -> int:
        """How many chunks are tagged with this project name."""
        self._ensure_connected()
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE project = ?",
            (str(project),),
        ).fetchone()
        return int(row[0]) if row else 0

    def list_projects(self) -> list[tuple[str, int]]:
        """Return (project_name, chunk_count) pairs present in the store."""
        self._ensure_connected()
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT project, COUNT(*) FROM chunks "
            "WHERE project IS NOT NULL AND project != '' "
            "GROUP BY project ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

    def stats(self) -> dict[str, Any]:
        """Return collection statistics."""
        self._ensure_connected()
        return {
            "collection": self._collection_name,
            "persist_dir": self._persist_dir,
            "backend": "turbovec",
            "total_chunks": self.count(),
            "index_path": str(self._index_path),
        }

    # ── Testing helper ──────────────────────────────────────────────

    def reset(self) -> None:
        """Delete the entire collection — used in tests only."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None
            self._index = None
            self._dirty = False
            for path in (self._index_path, self._db_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            # Also clear WAL companions
            for suffix in ("-wal", "-shm"):
                try:
                    Path(str(self._db_path) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass
            self._ensure_connected()
