"""File-level indexer with SHA-256 content hashing (SQLite index_state.db).

Converts source files into embedded chunks and stores them in the
vector database. Uses content hashing to avoid re-indexing unchanged files.
Applies secret/noise denylists and chunk-level secret scanning.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from rag.chunker import Chunker, CodeChunk
from rag.embeddings import EmbeddingModel
from rag.index_state_db import FileIndexEntry, IndexStateDB
from rag.security_filters import chunk_contains_secret, should_index_path
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

INDEX_STATE_DB = ".vector_db/index_state.db"


class Indexer:
    """Converts source files into embedded chunks stored in ChromaDB.

    Uses SHA-256 content hashing stored in SQLite: files are only
    re-embedded when their content actually changes.

    Parameters:
        vector_store: Target vector store for chunks.
        embedding_model: Model used to generate embeddings.
        chunker: Chunker used to split files.
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedding_model: EmbeddingModel | None = None,
        chunker: Chunker | None = None,
        state_file: str | Path | None = None,
    ) -> None:
        self._store = vector_store or VectorStore()
        self._embedder = embedding_model or EmbeddingModel()
        self._chunker = chunker or Chunker()
        if state_file is None:
            persist_dir = getattr(self._store, "_persist_dir", None)
            state_file = (
                Path(persist_dir) / "index_state.db"
                if persist_dir
                else INDEX_STATE_DB
            )
        else:
            # Accept legacy .json path and remap to .db beside it
            sp = Path(state_file)
            if sp.suffix.lower() == ".json":
                state_file = sp.with_suffix(".db")
        self._state = IndexStateDB(state_file)

    @property
    def state(self) -> IndexStateDB:
        """Access the underlying index state (for testing / inspection)."""
        return self._state

    # ── Public API ──────────────────────────────────────────────────

    def index_file(self, filepath: str | Path, project: str = "") -> int:
        """Index a single file. Skips if denied, secret-heavy, or unchanged.

        Args:
            filepath: Absolute or relative path to the source file.
            project: Project name for metadata tagging.

        Returns:
            Number of chunks indexed (0 if skipped).
        """
        filepath = str(Path(filepath).resolve())
        path_obj = Path(filepath)

        if not path_obj.exists():
            logger.warning("File not found, skipping: %s", filepath)
            return 0

        if not should_index_path(filepath):
            logger.info("Denied by security filter, skipping: %s", filepath)
            return 0

        # VRAM protection: skip files larger than 1MB
        MAX_FILE_SIZE = 1_048_576  # 1 MB
        try:
            file_size = path_obj.stat().st_size
            if file_size > MAX_FILE_SIZE:
                logger.info(
                    "Skipping oversized file (%d bytes): %s",
                    file_size, filepath,
                )
                return 0
        except OSError:
            pass

        try:
            content = path_obj.read_bytes()
        except OSError as exc:
            logger.error("Cannot read %s: %s", filepath, exc)
            return 0

        file_hash = hashlib.sha256(content).hexdigest()

        entry = self._state.get(filepath)
        if entry and entry.sha256 == file_hash:
            logger.debug("Unchanged, skipping: %s", filepath)
            return 0

        t0 = time.perf_counter()

        if entry:
            self._store.delete_by_file(filepath)

        chunks = self._chunker.chunk_file(filepath, project)
        # Drop chunks that contain secrets before embedding
        safe_chunks: list[CodeChunk] = []
        skipped_secrets = 0
        for c in chunks:
            blob = f"{c.to_search_text()}\n{c.content}"
            if chunk_contains_secret(blob):
                skipped_secrets += 1
                logger.warning(
                    "Skipping secret-bearing chunk in %s (%s L%s-%s)",
                    filepath,
                    c.chunk_type,
                    c.start_line,
                    c.end_line,
                )
                continue
            safe_chunks.append(c)

        if skipped_secrets:
            logger.info(
                "Secret scanner dropped %d chunk(s) from %s",
                skipped_secrets,
                filepath,
            )

        if not safe_chunks:
            logger.debug("No safe chunks produced for: %s", filepath)
            self._state.set(
                FileIndexEntry(
                    filepath=filepath,
                    sha256=file_hash,
                    last_indexed=time.time(),
                    chunk_count=0,
                )
            )
            self._state.save()
            return 0

        texts = [c.to_search_text() for c in safe_chunks]
        embeddings = self._embedder.encode(texts)

        ids = [c.chunk_id for c in safe_chunks]
        documents = [c.content for c in safe_chunks]
        metadatas = self._build_metadatas(safe_chunks)

        self._store.add(ids, embeddings, documents, metadatas)

        elapsed = time.perf_counter() - t0
        self._state.set(
            FileIndexEntry(
                filepath=filepath,
                sha256=file_hash,
                last_indexed=time.time(),
                chunk_count=len(safe_chunks),
                chunk_ids=ids,
            )
        )
        self._state.save()

        logger.info(
            "Indexed %s: %d chunks in %.2fs", filepath, len(safe_chunks), elapsed
        )
        return len(safe_chunks)

    def reindex_file(self, filepath: str | Path, project: str = "") -> int:
        """Force re-index of a file (ignores hash check)."""
        filepath = str(Path(filepath).resolve())
        entry = self._state.get(filepath)
        if entry:
            self._state.remove(filepath)
        return self.index_file(filepath, project)

    def remove_file(self, filepath: str | Path) -> int:
        """Remove all indexed chunks for a deleted file."""
        filepath = str(Path(filepath).resolve())
        count = self._store.delete_by_file(filepath)
        self._state.remove(filepath)
        self._state.save()
        logger.info("Removed %d chunks for deleted file: %s", count, filepath)
        return count

    def is_indexed(self, filepath: str | Path) -> bool:
        """Check whether a file is currently indexed."""
        filepath = str(Path(filepath).resolve())
        return self._state.get(filepath) is not None

    def needs_reindex(self, filepath: str | Path) -> bool:
        """Check whether a file's content has changed since last index."""
        filepath_str = str(Path(filepath).resolve())
        path_obj = Path(filepath_str)
        if not path_obj.exists():
            return False
        if not should_index_path(filepath_str):
            return False
        entry = self._state.get(filepath_str)
        if entry is None:
            return True
        try:
            current_hash = hashlib.sha256(path_obj.read_bytes()).hexdigest()
        except OSError:
            return False
        return current_hash != entry.sha256

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _build_metadatas(chunks: list[CodeChunk]) -> list[dict[str, Any]]:
        """Build ChromaDB-compatible metadata dicts from chunks."""
        metadatas: list[dict[str, Any]] = []
        for c in chunks:
            md: dict[str, Any] = {
                "filename": c.filename,
                "language": c.language,
                "chunk_type": c.chunk_type,
                "project": c.project,
                "start_line": c.start_line,
                "end_line": c.end_line,
            }
            if c.class_name:
                md["class_name"] = c.class_name
            if c.function_name:
                md["function_name"] = c.function_name
            if c.imports:
                md["imports"] = "; ".join(c.imports[:20])
            metadatas.append(md)
        return metadatas
