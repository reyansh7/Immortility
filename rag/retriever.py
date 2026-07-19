"""Top-level retrieval interface for the Knowledge Engine.

Orchestrates hybrid search and returns only the most relevant
code chunks for a given query.  The LLM never receives the entire
project — only the top results from the retriever.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from rag.embeddings import EmbeddingModel
from rag.hybrid_search import HybridSearch, SearchResult
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A single result returned by the Retriever."""

    content: str
    filename: str
    relevance_score: float
    chunk_type: str
    class_name: str = ""
    function_name: str = ""
    language: str = ""
    start_line: int = 0
    end_line: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_string(self) -> str:
        """Format this result for prompt injection."""
        header_parts: list[str] = [f"# {self.filename}"]
        if self.class_name:
            header_parts.append(f"  Class: {self.class_name}")
        if self.function_name:
            header_parts.append(f"  Function: {self.function_name}")
        if self.start_line:
            header_parts.append(f"  Lines: {self.start_line}–{self.end_line}")
        header = " | ".join(header_parts)
        return f"{header}\n{self.content}"


class Retriever:
    """Retrieves relevant code chunks for a natural-language query.

    Parameters:
        vector_store: ChromaDB vector store.
        embedding_model: BGE embedding model.
        hybrid_search: Pre-configured hybrid search (created internally
            if not provided).
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedding_model: EmbeddingModel | None = None,
        hybrid_search: HybridSearch | None = None,
    ) -> None:
        self._store = vector_store or VectorStore()
        self._embedder = embedding_model or EmbeddingModel()
        self._search = hybrid_search or HybridSearch(self._store, self._embedder)

    def retrieve(
        self,
        query: str,
        project: str | None = None,
        n_results: int = 10,
    ) -> list[RetrievalResult]:
        """Retrieve the most relevant code chunks for *query*.

        Args:
            query: Natural-language question or task description.
            project: Optional project name to scope the search.
            n_results: Maximum number of results.

        Returns:
            Ranked list of ``RetrievalResult`` objects.
        """
        t0 = time.perf_counter()

        search_results: list[SearchResult] = self._search.search(
            query, n_results=n_results, project=project
        )

        results = [self._to_retrieval_result(sr) for sr in search_results]

        elapsed = time.perf_counter() - t0
        logger.info(
            "Retriever: %d results for '%s' in %.3fs",
            len(results),
            query[:60],
            elapsed,
        )
        return results

    def invalidate_cache(self) -> None:
        """Invalidate the BM25 index (called after indexing changes)."""
        self._search.invalidate_bm25()

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _to_retrieval_result(sr: SearchResult) -> RetrievalResult:
        """Convert a ``SearchResult`` into a ``RetrievalResult``."""
        md = sr.metadata
        return RetrievalResult(
            content=sr.content,
            filename=sr.filename,
            relevance_score=sr.score,
            chunk_type=sr.chunk_type,
            class_name=md.get("class_name", ""),
            function_name=md.get("function_name", ""),
            language=md.get("language", ""),
            start_line=md.get("start_line", 0),
            end_line=md.get("end_line", 0),
            metadata=md,
        )
