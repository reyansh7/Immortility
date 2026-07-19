"""Context ranker — post-retrieval ranking, deduplication, and merging.

Runs between the Retriever and the Context Builder to ensure only
the highest-quality, non-redundant chunks reach the LLM.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from rag.retriever import RetrievalResult

logger = logging.getLogger(__name__)


@dataclass
class RankingConfig:
    """Weights for the context ranker."""

    relevance_weight: float = 0.5
    recency_weight: float = 0.2
    importance_weight: float = 0.2
    adjacency_bonus: float = 0.1


# Files that are inherently more important in most projects
IMPORTANT_FILE_PATTERNS: tuple[str, ...] = (
    "main.py", "app.py", "index.py", "server.py", "manage.py",
    "index.ts", "index.js", "app.ts", "app.js", "server.ts",
    "routes", "router", "middleware", "auth", "login",
    "README", "config", "settings", "database", "models",
)


class ContextRanker:
    """Re-ranks, deduplicates, and merges retrieval results.

    Operations applied in order:
    1. Remove exact duplicates.
    2. Merge adjacent chunks from the same file.
    3. Score by relevance × importance × recency.
    4. Prioritise the active project.
    5. Prioritise recently modified files.
    """

    def __init__(self, config: RankingConfig | None = None) -> None:
        self._config = config or RankingConfig()

    def rank(
        self,
        results: list[RetrievalResult],
        active_project: str = "",
        recently_modified: list[str] | None = None,
    ) -> list[RetrievalResult]:
        """Apply full ranking pipeline.

        Args:
            results: Raw retrieval results.
            active_project: Name of the currently active project.
            recently_modified: Paths of recently edited files.

        Returns:
            Ranked, deduplicated results.
        """
        if not results:
            return []

        recently_modified = recently_modified or []

        # Step 1: Deduplicate
        results = self._deduplicate(results)

        # Step 2: Merge adjacent chunks
        results = self._merge_adjacent(results)

        # Step 3: Score
        scored = self._score(results, active_project, recently_modified)

        # Step 4: Sort descending by final score
        scored.sort(key=lambda r: r.relevance_score, reverse=True)

        logger.debug(
            "Ranked %d results (active_project=%s)", len(scored), active_project
        )
        return scored

    # ── Deduplication ───────────────────────────────────────────────

    @staticmethod
    def _deduplicate(results: list[RetrievalResult]) -> list[RetrievalResult]:
        """Remove results with identical content."""
        seen: set[str] = set()
        unique: list[RetrievalResult] = []
        for r in results:
            key = r.content[:300]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    # ── Adjacent chunk merging ──────────────────────────────────────

    @staticmethod
    def _merge_adjacent(results: list[RetrievalResult]) -> list[RetrievalResult]:
        """Merge chunks from the same file that are adjacent in line numbers."""
        if len(results) <= 1:
            return results

        # Group by filename
        by_file: dict[str, list[RetrievalResult]] = {}
        for r in results:
            by_file.setdefault(r.filename, []).append(r)

        merged: list[RetrievalResult] = []
        for filename, group in by_file.items():
            group.sort(key=lambda r: r.start_line)
            current = group[0]

            for i in range(1, len(group)):
                nxt = group[i]
                # Adjacent if start of next is within 3 lines of end of current
                if nxt.start_line <= current.end_line + 3:
                    # Merge
                    combined = current.content + "\n" + nxt.content
                    current = RetrievalResult(
                        content=combined,
                        filename=filename,
                        relevance_score=max(
                            current.relevance_score, nxt.relevance_score
                        ),
                        chunk_type=current.chunk_type,
                        class_name=current.class_name or nxt.class_name,
                        function_name=current.function_name or nxt.function_name,
                        language=current.language,
                        start_line=current.start_line,
                        end_line=max(current.end_line, nxt.end_line),
                        metadata=current.metadata,
                    )
                else:
                    merged.append(current)
                    current = nxt
            merged.append(current)

        return merged

    # ── Scoring ─────────────────────────────────────────────────────

    def _score(
        self,
        results: list[RetrievalResult],
        active_project: str,
        recently_modified: list[str],
    ) -> list[RetrievalResult]:
        """Assign a final composite score to each result."""
        cfg = self._config
        recent_set = set(recently_modified)

        for r in results:
            base = r.relevance_score

            # Importance bonus
            importance = self._file_importance(r.filename)

            # Recency bonus
            recency = 1.0 if r.filename in recent_set else 0.0

            # Project bonus — prioritise the active project
            project_bonus = 0.0
            if active_project and r.metadata.get("project") == active_project:
                project_bonus = 0.15

            final = (
                base * cfg.relevance_weight
                + importance * cfg.importance_weight
                + recency * cfg.recency_weight
                + project_bonus
            )
            r.relevance_score = final

        return results

    @staticmethod
    def _file_importance(filename: str) -> float:
        """Heuristic importance score for a filename (0–1)."""
        basename = os.path.basename(filename).lower()
        for pattern in IMPORTANT_FILE_PATTERNS:
            if pattern.lower() in basename:
                return 1.0
        return 0.3
