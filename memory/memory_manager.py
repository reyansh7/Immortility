"""Central memory coordinator — single interface for all memory subsystems.

Routes store / recall / forget operations to the appropriate memory
type and enforces secret-filtering rules.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from memory.conversation_memory import ConversationMemory
from memory.preference_memory import PreferenceMemory
from memory.project_memory import ProjectMemory
from memory.session_memory import SessionMemory

logger = logging.getLogger(__name__)

# Patterns that must NEVER be stored
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(access[_-]?key|secret|credential)\s*[:=]\s*\S+"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
)


class MemoryManager:
    """Unified interface for all memory subsystems.

    Provides store / recall / forget / summary operations routed
    to the correct memory type.  All writes are filtered for secrets
    before persistence.
    """

    def __init__(self) -> None:
        self.conversation = ConversationMemory()
        self.project = ProjectMemory()
        self.preferences = PreferenceMemory()
        self.session = SessionMemory()

    # ── Store ───────────────────────────────────────────────────────

    def store(self, category: str, key: str, value: Any) -> bool:
        """Store a memory entry.

        Args:
            category: One of "conversation", "project", "preference", "session".
            key: Entry key or sub-type.
            value: Value to store.

        Returns:
            True if stored successfully, False if rejected (e.g. secret).
        """
        # Secret filtering
        text_repr = str(value)
        if self._contains_secret(text_repr):
            logger.warning(
                "Refused to store potential secret in %s/%s", category, key
            )
            return False

        category = category.lower()
        if category == "conversation":
            self.conversation.add_note(f"{key}: {value}")
        elif category == "project":
            if isinstance(value, dict):
                self.project.remember_project(value)
            else:
                self.project.remember_project({"name": key, "notes": str(value)})
        elif category == "preference":
            self.preferences.set_preference(key, value)
        elif category == "session":
            self.session.set(key, value)
        else:
            logger.warning("Unknown memory category: %s", category)
            return False

        logger.debug("Stored in %s: %s", category, key)
        return True

    # ── Recall ──────────────────────────────────────────────────────

    def recall(self, category: str, query: str = "") -> Any:
        """Retrieve memory entries.

        Args:
            category: Memory type to search.
            query: Optional search query.

        Returns:
            Retrieved data (type depends on category).
        """
        category = category.lower()
        if category == "conversation":
            if query:
                return self.conversation.search(query)
            return self.conversation.get_recent()
        elif category == "project":
            if query:
                return self.project.get_project(query)
            return self.project.list_projects()
        elif category == "preference":
            if query:
                return self.preferences.get_preference(query)
            return self.preferences.get_all()
        elif category == "session":
            if query:
                return self.session.get(query)
            return self.session.keys()
        else:
            logger.warning("Unknown memory category: %s", category)
            return None

    # ── Forget ──────────────────────────────────────────────────────

    def forget(self, category: str, key: str) -> bool:
        """Remove a specific memory entry.

        Args:
            category: Memory type.
            key: Key to remove.

        Returns:
            True if something was removed.
        """
        category = category.lower()
        if category == "project":
            return self.project.forget_project(key)
        elif category == "preference":
            return self.preferences.delete_preference(key)
        elif category == "session":
            return self.session.delete(key)
        elif category == "conversation":
            logger.info("Conversation memory does not support targeted deletion.")
            return False
        else:
            logger.warning("Unknown memory category: %s", category)
            return False

    # ── Summary ─────────────────────────────────────────────────────

    def get_full_summary(self, project_name: str = "") -> str:
        """Assemble a combined memory summary for context injection.

        Args:
            project_name: Optionally scope to a specific project.

        Returns:
            Formatted summary string.
        """
        parts: list[str] = []

        proj = self.project.to_summary(project_name) if project_name else self.project.to_summary()
        if proj:
            parts.append(proj)

        prefs = self.preferences.to_summary()
        if prefs:
            parts.append(prefs)

        conv = self.conversation.to_summary(n=5)
        if conv:
            parts.append(conv)

        sess = self.session.to_summary()
        if sess:
            parts.append(sess)

        return "\n\n".join(parts)

    # ── Lifecycle ───────────────────────────────────────────────────

    def clear_session(self) -> None:
        """Clear session memory (called on application exit)."""
        self.session.clear()

    # ── Secret detection ────────────────────────────────────────────

    @staticmethod
    def _contains_secret(text: str) -> bool:
        """Check whether *text* contains password / API key / secret patterns."""
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                return True
        return False
