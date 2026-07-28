"""Central memory coordinator — single interface for all memory subsystems.

Routes store / recall / forget operations to the appropriate memory
type and enforces secret-filtering rules.
"""

from __future__ import annotations

import logging
from typing import Any

from memory.conversation_memory import ConversationMemory
from memory.preference_memory import PreferenceMemory
from memory.project_memory import ProjectMemory
from memory.session_memory import SessionMemory
from rag.security_filters import chunk_contains_secret

logger = logging.getLogger(__name__)


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
        """Retrieve memory entries."""
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
        """Remove a specific memory entry."""
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
        """Build a combined summary of all memory for context injection."""
        parts: list[str] = []

        prefs = self.preferences.to_summary()
        if prefs:
            parts.append(prefs)

        proj = self.project.to_summary()
        if proj:
            parts.append(proj)

        conv = self.conversation.to_summary()
        if conv:
            parts.append(conv)

        sess = self.session.to_summary()
        if sess:
            parts.append(sess)

        return "\n\n".join(parts)

    # ── Conversation extraction (moved from MemoryAgent) ────────────

    def extract_from_conversation(self, messages: list[dict[str, str]]) -> None:
        """Analyse conversation messages and auto-extract memories."""
        for msg in messages:
            content = msg.get("content", "").lower()
            role = msg.get("role", "")

            if role != "assistant":
                continue

            if any(
                phrase in content
                for phrase in ("task complete", "done:", "successfully", "created file")
            ):
                self.conversation.add_task(msg["content"][:200])

            if any(
                phrase in content
                for phrase in ("fixed", "bug fix", "resolved", "patched")
            ):
                self.conversation.add_bug_fix(msg["content"][:200])

    async def auto_learn(self, user_input: str) -> None:
        """Extract facts/preferences from user input after a reply."""
        import asyncio

        from core.json_utils import parse_llm_json
        from core.llm import chat

        prompt = f"""You are a background memory extraction agent. Analyze the user's message.
Extract any explicit user preferences, personal facts (e.g. name, role, tech stack), or project details that the user shares.
Output ONLY a JSON array of objects with keys: "category" ("preference", "project"), "key" (short name), "value" (the fact/preference).
If there is no new personal fact, preference, or project detail to remember, you MUST output an empty array [].

Examples:
Input: "my name is reyansh" -> [{{"category": "preference", "key": "user_name", "value": "reyansh"}}]
Input: "i prefer python for backend" -> [{{"category": "preference", "key": "backend_language", "value": "python"}}]
Input: "what is the weather?" -> []
Input: "fix this bug" -> []

User message: {user_input}"""

        try:
            response = await asyncio.to_thread(
                chat,
                model="auto",
                messages=[{"role": "user", "content": prompt}],
                format="json",
                think=False,
                options={"temperature": 0.1},
            )
            content = response["message"]["content"].strip()
            data = parse_llm_json(content)

            if isinstance(data, dict):
                data = data.get("items", data.get("results", []))

            if isinstance(data, list):
                for item in data:
                    cat = item.get("category")
                    k = item.get("key")
                    v = item.get("value")
                    if cat and k and v:
                        self.store(cat, k, v)
                        logger.info("Auto-learned: %s -> %s = %s", cat, k, v)
                        if cat in ("preference", "project"):
                            try:
                                from knowledge.engine import KnowledgeEngine

                                ke = KnowledgeEngine()
                                proj = ke.get_active_project_name() or "global"
                                ke._kg_db.set_fact(proj, str(k), str(v)[:500])
                            except Exception:
                                pass
        except Exception as e:
            logger.debug("Auto-learn failed: %s", e)

    # ── Lifecycle ───────────────────────────────────────────────────

    def clear_session(self) -> None:
        """Clear session memory (called on application exit)."""
        self.session.clear()

    # ── Secret detection ────────────────────────────────────────────

    @staticmethod
    def _contains_secret(text: str) -> bool:
        """Check whether *text* contains password / API key / secret patterns."""
        return chunk_contains_secret(text)
