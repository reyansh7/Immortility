"""Memory agent — intelligent interface for memory operations.

Provides store / retrieve / summarize / forget / update methods
with automatic secret filtering and conversation history extraction.
"""

from __future__ import annotations

import logging
from typing import Any

from memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class MemoryAgent:
    """High-level memory agent used by the Knowledge Engine.

    Wraps ``MemoryManager`` with additional intelligence:
    - Extracts memories from conversation history
    - Summarises memory for context injection
    - Enforces security rules (never stores secrets)
    """

    def __init__(self, memory_manager: MemoryManager | None = None) -> None:
        self._memory = memory_manager or MemoryManager()

    @property
    def manager(self) -> MemoryManager:
        """Direct access to the underlying MemoryManager."""
        return self._memory

    # ── Store ───────────────────────────────────────────────────────

    def store(self, category: str, key: str, value: Any) -> bool:
        """Store a memory entry (with secret filtering).

        Args:
            category: "conversation", "project", "preference", or "session".
            key: Entry key.
            value: Value to store.

        Returns:
            True if stored, False if rejected.
        """
        return self._memory.store(category, key, value)

    # ── Retrieve ────────────────────────────────────────────────────

    def retrieve(self, category: str, query: str = "") -> Any:
        """Retrieve memory entries.

        Args:
            category: Memory type to search.
            query: Optional search query.

        Returns:
            Retrieved data.
        """
        return self._memory.recall(category, query)

    # ── Summarise ───────────────────────────────────────────────────

    def summarize(self, project_name: str = "") -> str:
        """Get a combined memory summary for context injection.

        Args:
            project_name: Optionally scope to a specific project.

        Returns:
            Formatted summary string.
        """
        return self._memory.get_full_summary(project_name)

    # ── Forget ──────────────────────────────────────────────────────

    def forget(self, category: str, key: str) -> bool:
        """Remove a specific memory entry.

        Args:
            category: Memory type.
            key: Key to forget.

        Returns:
            True if something was removed.
        """
        return self._memory.forget(category, key)

    # ── Update ──────────────────────────────────────────────────────

    def update(self, category: str, key: str, value: Any) -> bool:
        """Update an existing memory entry (store with overwrite semantics).

        Args:
            category: Memory type.
            key: Entry key.
            value: New value.

        Returns:
            True if updated.
        """
        return self._memory.store(category, key, value)

    # ── Conversation extraction ─────────────────────────────────────

    def extract_from_conversation(
        self, messages: list[dict[str, str]]
    ) -> None:
        """Analyse conversation messages and auto-extract memories.

        Looks for completed tasks, bug fixes, and preferences in
        the conversation history.

        Args:
            messages: List of {role, content} message dicts.
        """
        for msg in messages:
            content = msg.get("content", "").lower()
            role = msg.get("role", "")

            if role != "assistant":
                continue

            # Detect completed tasks
            if any(
                phrase in content
                for phrase in ("task complete", "done:", "successfully", "created file")
            ):
                summary = msg["content"][:200]
                self._memory.conversation.add_task(summary)

            # Detect bug fixes
            if any(
                phrase in content
                for phrase in ("fixed", "bug fix", "resolved", "patched")
            ):
                summary = msg["content"][:200]
                self._memory.conversation.add_bug_fix(summary)

    async def auto_learn(self, user_input: str) -> None:
        """Extract facts, preferences, and project details from user input.

        Designed to be called *after* the main LLM response has finished
        streaming, NOT concurrently via ``asyncio.create_task``.

        Qwen3 8B adjustments:
        - ``think=False`` disables the native reasoning mode so the model
          immediately outputs JSON without hidden ``<think>`` blocks.
        - ``format="json"`` enforces strict JSON output.
        - Temperature 0.1 for deterministic extraction.
        """
        import asyncio
        import json
        import re
        from ollama import chat

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
                model="qwen3:8b",
                messages=[{"role": "user", "content": prompt}],
                format="json",
                think=False,
                options={"temperature": 0.1},
            )
            content = response["message"]["content"].strip()

            # Robust markdown fence stripping
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip()

            data = json.loads(content)

            # Handle both {"items": [...]} wrapper and plain [...]
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
                        # Mirror project facts into knowledge graph when possible
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
        """Clear session-only memory."""
        self._memory.clear_session()
