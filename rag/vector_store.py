"""Persistent ChromaDB vector store for Immortility.

Wraps ChromaDB with a clean interface for adding, searching, and
removing code chunks.  The database is stored on disk in ``vector_db/``
and survives restarts without requiring a rebuild.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# pyrefly: ignore [missing-import]
import chromadb

logger = logging.getLogger(__name__)

CODE_COLLECTION = "code_chunks"
DOCS_COLLECTION = "documentation"


def _default_persist_dir() -> str:
    try:
        from core.repo_paths import vector_db_dir

        return str(vector_db_dir())
    except Exception:
        return ".vector_db"


DEFAULT_PERSIST_DIR = _default_persist_dir()


class VectorStore:
    """Persistent local vector store backed by ChromaDB.

    Parameters:
        persist_dir: Directory for ChromaDB on-disk storage.
        collection_name: Name of the collection to use.
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str = CODE_COLLECTION,
    ) -> None:
        self._persist_dir = persist_dir or _default_persist_dir()
        self._collection_name = collection_name
        self._client: chromadb.ClientAPI | None = None
        self._collection: Any = None

    # ── Lazy connection ─────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        """Connect to / create the persistent ChromaDB client on first use."""
        if self._client is not None:
            return

        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        count = self._collection.count()
        logger.info(
            "ChromaDB connected: dir=%s collection=%s chunks=%d",
            self._persist_dir,
            self._collection_name,
            count,
        )

    # ── CRUD operations ─────────────────────────────────────────────

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Upsert chunks into the collection.

        Args:
            ids: Unique chunk identifiers.
            embeddings: Pre-computed embedding vectors.
            documents: Raw text content of each chunk.
            metadatas: Per-chunk metadata dictionaries.
        """
        self._ensure_connected()
        if not ids:
            return

        # ChromaDB has a batch-size limit; use 500 to prevent memory spikes
        batch_size = 500
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            self._collection.upsert(
                ids=ids[i:end],
                embeddings=embeddings[i:end],
                documents=documents[i:end],
                metadatas=metadatas[i:end],
            )
        logger.debug("Upserted %d chunks.", len(ids))

    def search(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search by embedding similarity.

        Args:
            query_embedding: The query vector.
            n_results: Maximum results to return.
            where: Optional ChromaDB metadata filter.

        Returns:
            List of dicts with keys: id, document, metadata, distance.
        """
        self._ensure_connected()
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(n_results, max(self._collection.count(), 1)),
        }
        if where:
            kwargs["where"] = where

        try:
            raw = self._collection.query(**kwargs)
        except Exception as exc:
            logger.error("ChromaDB query failed: %s", exc)
            return []

        results: list[dict[str, Any]] = []
        if not raw["ids"] or not raw["ids"][0]:
            return results

        for idx in range(len(raw["ids"][0])):
            results.append(
                {
                    "id": raw["ids"][0][idx],
                    "document": (raw["documents"] or [[]])[0][idx] if raw.get("documents") else "",
                    "metadata": (raw["metadatas"] or [[]])[0][idx] if raw.get("metadatas") else {},
                    "distance": (raw["distances"] or [[]])[0][idx] if raw.get("distances") else 1.0,
                }
            )
        return results

    def delete_by_file(self, filepath: str) -> int:
        """Remove all chunks belonging to a specific file.

        Args:
            filepath: The file whose chunks should be deleted.

        Returns:
            Number of chunks removed.
        """
        self._ensure_connected()
        try:
            existing = self._collection.get(
                where={"filename": filepath},
                include=[],
            )
            ids = existing["ids"]
            if ids:
                self._collection.delete(ids=ids)
                logger.debug("Deleted %d chunks for %s", len(ids), filepath)
            return len(ids)
        except Exception as exc:
            logger.error("Delete failed for %s: %s", filepath, exc)
            return 0

    def delete_by_project(self, project: str) -> int:
        """Remove all chunks belonging to a project.

        Args:
            project: Project name whose chunks should be deleted.

        Returns:
            Number of chunks removed.
        """
        self._ensure_connected()
        try:
            existing = self._collection.get(
                where={"project": project},
                include=[],
            )
            ids = existing["ids"]
            if ids:
                self._collection.delete(ids=ids)
                logger.debug("Deleted %d chunks for project %s", len(ids), project)
            return len(ids)
        except Exception as exc:
            logger.error("Delete failed for project %s: %s", project, exc)
            return 0

    def get_all_documents(self) -> list[dict[str, Any]]:
        """Return all documents in the collection (for BM25 index building).

        Returns:
            List of dicts with keys: id, document, metadata.
        """
        self._ensure_connected()
        count = self._collection.count()
        if count == 0:
            return []
        raw = self._collection.get(include=["documents", "metadatas"])
        results: list[dict[str, Any]] = []
        for i, doc_id in enumerate(raw["ids"]):
            results.append(
                {
                    "id": doc_id,
                    "document": raw["documents"][i] if raw["documents"] else "",
                    "metadata": raw["metadatas"][i] if raw["metadatas"] else {},
                }
            )
        return results

    # ── Stats ───────────────────────────────────────────────────────

    def count(self) -> int:
        """Return the number of chunks in the collection."""
        self._ensure_connected()
        return self._collection.count()

    def stats(self) -> dict[str, Any]:
        """Return collection statistics."""
        self._ensure_connected()
        total = self._collection.count()
        return {
            "collection": self._collection_name,
            "persist_dir": self._persist_dir,
            "total_chunks": total,
        }

    # ── Testing helper ──────────────────────────────────────────────

    def reset(self) -> None:
        """Delete the entire collection — used in tests only."""
        self._ensure_connected()
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
