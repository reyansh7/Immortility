"""Experience Memory — Stores successful bug fixes and task takeaways.

This forms the foundation of Phase 3 (Autonomous Learning).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class ExperienceEntry:
    error_message: str
    root_cause: str
    fix_applied: str
    file_path: str


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def format_experiences_block(entries: list[ExperienceEntry], *, limit: int = 3) -> str:
    """Prompt-ready PAST EXPERIENCES block."""
    if not entries:
        return ""
    lines = ["PAST EXPERIENCES (learn from these; avoid repeating failed approaches):"]
    for e in entries[:limit]:
        err = (e.error_message or "").strip().replace("\n", " ")[:160]
        fix = (e.fix_applied or "").strip().replace("\n", " ")[:160]
        cause = (e.root_cause or "").strip().replace("\n", " ")[:80]
        path = (e.file_path or "").strip()
        lines.append(f"- Error/task: {err}")
        if cause:
            lines.append(f"  Outcome/cause: {cause}")
        if fix:
            lines.append(f"  Fix/lesson: {fix}")
        if path:
            lines.append(f"  File: {path}")
    return "\n".join(lines)


class ExperienceMemory:
    """Lightweight database to store and retrieve Bug/Fix experiences."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            try:
                from core.repo_paths import experience_db_path

                db_path = experience_db_path()
            except Exception:
                db_path = Path(".immortility/experience.json")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: List[ExperienceEntry] = self._load()

    def _load(self) -> List[ExperienceEntry]:
        if not self.db_path.exists():
            return []
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
            return [ExperienceEntry(**item) for item in data]
        except Exception as e:
            logger.error("Failed to load experience DB: %s", e)
            return []

    def record_bug_fix(self, error_message: str, file_path: str, fix_applied: str) -> None:
        """Record a bug and how it was successfully fixed."""
        entry = ExperienceEntry(
            error_message=error_message,
            root_cause="Automatically inferred from fix",
            fix_applied=fix_applied,
            file_path=file_path,
        )
        self._cache.append(entry)
        self._save()
        logger.info("Recorded new bug fix experience for %s", file_path)

    def record(self, task: str, outcome: str, details: str = "", file_path: str = "") -> None:
        """Record a workflow task outcome."""
        entry = ExperienceEntry(
            error_message=task[:500],
            root_cause=outcome,
            fix_applied=details[:1000],
            file_path=file_path or "workflow",
        )
        self._cache.append(entry)
        self._save()

    def _save(self) -> None:
        try:
            data = [asdict(entry) for entry in self._cache]
            self.db_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save experience DB: %s", e)

    def clear_memory(self) -> None:
        """Empty the cache and save the empty state to disk."""
        self._cache.clear()
        self._save()
        logger.info("Experience memory cleared.")

    def _keyword_score(self, query: str, entry: ExperienceEntry) -> float:
        query_terms = set(query.lower().split())
        text = (
            f"{entry.error_message} {entry.root_cause} "
            f"{entry.fix_applied} {entry.file_path}"
        ).lower()
        score = 0.0
        for term in query_terms:
            if len(term) >= 2 and term in text:
                score += 1.0 if len(term) <= 3 else 2.0
        return score

    def get_relevant_experiences(self, query: str, limit: int = 3) -> List[ExperienceEntry]:
        """Retrieve relevant past experiences (embedding similarity, keyword fallback)."""
        if not query or not self._cache:
            return []

        # Prefer embedding similarity when the model is available
        try:
            from rag.embeddings import EmbeddingModel

            model = EmbeddingModel()
            q_emb = model.encode_query(query)
            scored: list[tuple[float, ExperienceEntry]] = []
            for entry in self._cache:
                passage = (
                    f"{entry.error_message}\n{entry.root_cause}\n"
                    f"{entry.fix_applied}\n{entry.file_path}"
                )
                e_emb = model.encode([passage])[0]
                sim = _cosine(q_emb, e_emb)
                if sim >= 0.45:
                    scored.append((sim, entry))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                return [e for _, e in scored[:limit]]
        except Exception as exc:
            logger.debug("Experience embedding retrieval failed, keyword fallback: %s", exc)

        scored_kw: list[tuple[float, ExperienceEntry]] = []
        for entry in self._cache:
            score = self._keyword_score(query, entry)
            if score > 0:
                scored_kw.append((score, entry))
        scored_kw.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored_kw[:limit]]
