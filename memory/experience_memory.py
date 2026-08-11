"""Experience Memory — Stores successful bug fixes and task takeaways.

This forms the foundation of Phase 3 (Autonomous Learning).
"""

import json
import logging
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
            file_path=file_path
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

    def get_relevant_experiences(self, query: str, limit: int = 3) -> List[ExperienceEntry]:
        """Keyword matching for relevant past bug/fix experiences."""
        if not query:
            return []

        query_terms = set(query.lower().split())
        scored = []
        for entry in self._cache:
            score = 0
            text = (
                f"{entry.error_message} {entry.root_cause} "
                f"{entry.fix_applied} {entry.file_path}"
            ).lower()
            for term in query_terms:
                # Include short technical tokens (css, bug, jwt, etc.)
                if len(term) >= 2 and term in text:
                    score += 1 if len(term) <= 3 else 2
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]
