"""Hybrid search combining semantic (TurboVec) and keyword (BM25) retrieval.

Results are merged and re-ranked using a weighted formula that considers
semantic similarity, keyword match, file importance and recency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from rank_bm25 import BM25Okapi

from rag.embeddings import EmbeddingModel
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result from hybrid retrieval."""

    content: str
    filename: str
    score: float
    chunk_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # "semantic" | "keyword" | "both"


class HybridSearch:
    """Combines semantic (TurboVec) and keyword (BM25) search.

    Parameters:
        vector_store: TurboVec-backed vector store.
        embedding_model: BGE embedding model.
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self._store = vector_store or VectorStore()
        self._embedder = embedding_model or EmbeddingModel()
        self._bm25: BM25Okapi | None = None
        self._bm25_docs: list[dict[str, Any]] = []
        self._bm25_stale = True

    # ── BM25 index management ───────────────────────────────────────

    def invalidate_bm25(self) -> None:
        """Mark the BM25 index as stale (will be rebuilt on next search)."""
        self._bm25_stale = True

    def _rebuild_bm25(self) -> None:
        """Rebuild the BM25 index from all documents in the vector store."""
        if not self._bm25_stale and self._bm25 is not None:
            return

        t0 = time.perf_counter()
        self._bm25_docs = self._store.get_all_documents()

        if not self._bm25_docs:
            self._bm25 = None
            self._bm25_stale = False
            return

        tokenised = [
            doc["document"].lower().split() for doc in self._bm25_docs
        ]
        self._bm25 = BM25Okapi(tokenised)
        self._bm25_stale = False
        elapsed = time.perf_counter() - t0
        logger.info(
            "BM25 index rebuilt: %d documents in %.2fs",
            len(self._bm25_docs),
            elapsed,
        )

    # ── Search ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        n_results: int = 10,
        project: str | None = None,
        semantic_weight: float = 0.6,
        keyword_weight: float = 0.4,
    ) -> list[SearchResult]:
        """Run hybrid search and return ranked results.

        Args:
            query: Natural-language search query.
            n_results: Maximum results to return.
            project: Optional project filter.
            semantic_weight: Weight for semantic scores (0–1).
            keyword_weight: Weight for keyword scores (0–1).

        Returns:
            Ranked list of ``SearchResult`` objects.
        """
        t0 = time.perf_counter()

        # ── Semantic search ─────────────────────────────────────────
        semantic_results = self._semantic_search(query, n_results * 2, project)

        # ── Keyword search ──────────────────────────────────────────
        keyword_results = self._keyword_search(query, n_results * 2, project)

        # ── Merge and rank ──────────────────────────────────────────
        merged = self._merge_results(
            semantic_results,
            keyword_results,
            semantic_weight,
            keyword_weight,
        )

        # ── Filter by project if needed ─────────────────────────────
        if project:
            merged = [
                r for r in merged
                if r.metadata.get("project", "") == project
            ]

        # ── Remove duplicates ───────────────────────────────────────
        merged = self._deduplicate(merged)

        elapsed = time.perf_counter() - t0
        logger.info(
            "Hybrid search for '%s': %d results in %.3fs",
            query[:60],
            len(merged[:n_results]),
            elapsed,
        )

        return merged[:n_results]

    # ── Internals ───────────────────────────────────────────────────

    def _semantic_search(
        self, query: str, n: int, project: str | None
    ) -> list[SearchResult]:
        """Run semantic (embedding) search against TurboVec."""
        embedding = self._embedder.encode_query(query)
        where = {"project": project} if project else None
        raw = self._store.search(embedding, n_results=n, where=where)

        # Collect higher-is-better scores (TurboVec), else 1 - chroma-style distance
        scored: list[tuple[float, dict[str, Any]]] = []
        for hit in raw:
            if hit.get("score") is not None:
                s = float(hit["score"])
            else:
                s = max(0.0, 1.0 - float(hit.get("distance", 1.0)))
            scored.append((s, hit))
        if not scored:
            return []

        max_s = max(s for s, _ in scored)
        min_s = min(s for s, _ in scored)
        span = max_s - min_s

        results: list[SearchResult] = []
        for s, hit in scored:
            if span > 1e-9:
                similarity = (s - min_s) / span
            else:
                similarity = 1.0
            results.append(
                SearchResult(
                    content=hit["document"],
                    filename=hit["metadata"].get("filename", ""),
                    score=similarity,
                    chunk_type=hit["metadata"].get("chunk_type", ""),
                    metadata=hit["metadata"],
                    source="semantic",
                )
            )
        return results

    def _keyword_search(self, query: str, n: int, project: str | None = None) -> list[SearchResult]:
        """Run BM25 keyword search, optionally scoped to a project."""
        self._rebuild_bm25()
        if self._bm25 is None or not self._bm25_docs:
            return []

        docs = self._bm25_docs
        if project:
            docs = [d for d in docs if d.get("metadata", {}).get("project", "") == project]
            if not docs:
                return []

        tokens = query.lower().split()
        if not tokens:
            return []

        if project:
            tokenised = [doc["document"].lower().split() for doc in docs]
            bm25 = BM25Okapi(tokenised)
            scores = bm25.get_scores(tokens)
            doc_list = docs
        else:
            scores = self._bm25.get_scores(tokens)
            doc_list = self._bm25_docs

        # Collect top-n by BM25 score
        scored = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:n]

        # Normalise scores to 0–1
        max_score = scored[0][1] if scored and scored[0][1] > 0 else 1.0

        results: list[SearchResult] = []
        for idx, score in scored:
            if score <= 0:
                continue
            doc = doc_list[idx]
            results.append(
                SearchResult(
                    content=doc["document"],
                    filename=doc["metadata"].get("filename", ""),
                    score=score / max_score,
                    chunk_type=doc["metadata"].get("chunk_type", ""),
                    metadata=doc["metadata"],
                    source="keyword",
                )
            )
        return results

    @staticmethod
    def _merge_results(
        semantic: list[SearchResult],
        keyword: list[SearchResult],
        sw: float,
        kw: float,
    ) -> list[SearchResult]:
        """Merge two result lists using weighted scoring."""
        by_key: dict[str, SearchResult] = {}

        for r in semantic:
            key = f"{r.filename}:{r.metadata.get('start_line', 0)}"
            if key not in by_key:
                by_key[key] = SearchResult(
                    content=r.content,
                    filename=r.filename,
                    score=0.0,
                    chunk_type=r.chunk_type,
                    metadata=r.metadata,
                    source="semantic",
                )
            by_key[key].score += r.score * sw

        for r in keyword:
            key = f"{r.filename}:{r.metadata.get('start_line', 0)}"
            if key not in by_key:
                by_key[key] = SearchResult(
                    content=r.content,
                    filename=r.filename,
                    score=0.0,
                    chunk_type=r.chunk_type,
                    metadata=r.metadata,
                    source="keyword",
                )
                by_key[key].score += r.score * kw
            else:
                by_key[key].score += r.score * kw
                by_key[key].source = "both"

        ranked = sorted(by_key.values(), key=lambda r: r.score, reverse=True)
        return ranked

    @staticmethod
    def _deduplicate(results: list[SearchResult]) -> list[SearchResult]:
        """Remove results with identical content."""
        seen: set[str] = set()
        unique: list[SearchResult] = []
        for r in results:
            content_key = r.content[:200]
            if content_key not in seen:
                seen.add(content_key)
                unique.append(r)
        return unique
