"""Token-aware context builder for Qwen3:8B.

Assembles the final context from retrieved chunks, conversation
history, project metadata, and memory — while keeping the total
size within the model's effective budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rag.retriever import RetrievalResult

logger = logging.getLogger(__name__)

# Qwen3:8B has 32K context, but quality degrades with long prompts.
# We reserve budget for: system prompt (~200), conversation (~1500),
# model response (~2000), leaving ~4000 tokens for RAG context.
DEFAULT_CONTEXT_BUDGET = 6000
CHARS_PER_TOKEN = 4  # Conservative estimate for English + code


@dataclass
class ContextParts:
    """All ingredients used to build the final context string."""

    retrieved_chunks: list[RetrievalResult]
    project_summary: str = ""
    conversation_summary: str = ""
    user_preferences: str = ""
    memory_notes: str = ""


class ContextBuilder:
    """Assembles a token-budgeted context string for the LLM.

    Parameters:
        context_budget: Maximum token budget for RAG context.
    """

    def __init__(self, context_budget: int = DEFAULT_CONTEXT_BUDGET) -> None:
        self._budget = context_budget
        self._max_chars = context_budget * CHARS_PER_TOKEN

    def build(self, parts: ContextParts) -> str:
        """Build the final context string within budget.

        Priority order:
        1. Project summary (always included — very small)
        2. User preferences (always included — very small)
        3. Memory notes (capped)
        4. Retrieved chunks (largest portion, truncated to fit)
        5. Conversation summary (remaining budget)

        Args:
            parts: All context ingredients.

        Returns:
            A formatted context string.
        """
        sections: list[str] = []
        remaining = self._max_chars

        # 1. Project summary — high priority, small
        if parts.project_summary:
            section = f"[Project]\n{parts.project_summary}"
            sections.append(section)
            remaining -= len(section)

        # 2. User preferences — high priority, small
        if parts.user_preferences:
            section = f"[Preferences]\n{parts.user_preferences}"
            sections.append(section)
            remaining -= len(section)

        # 3. Memory notes — medium priority
        if parts.memory_notes:
            budget_for_memory = min(remaining // 6, 800)
            truncated = parts.memory_notes[:budget_for_memory]
            section = f"[Memory]\n{truncated}"
            sections.append(section)
            remaining -= len(section)

        # 4. Retrieved chunks — largest allocation
        chunk_budget = int(remaining * 0.75)
        if parts.retrieved_chunks:
            chunk_section = self._format_chunks(
                parts.retrieved_chunks, chunk_budget
            )
            if chunk_section:
                sections.append(chunk_section)
                remaining -= len(chunk_section)

        # 5. Conversation summary — fills remaining space
        if parts.conversation_summary:
            conv_budget = min(remaining, 1200)
            truncated = parts.conversation_summary[:conv_budget]
            section = f"[Recent Context]\n{truncated}"
            sections.append(section)

        context = "\n\n".join(sections)
        token_estimate = len(context) // CHARS_PER_TOKEN
        logger.info(
            "Context built: ~%d tokens (%d chars), %d chunks included",
            token_estimate,
            len(context),
            len(parts.retrieved_chunks),
        )
        return context

    def build_from_query(
        self,
        query: str,
        retrieval_results: list[RetrievalResult],
        project_summary: str = "",
        preferences: str = "",
        memory: str = "",
        conversation: str = "",
    ) -> str:
        """Convenience method: build context from individual arguments.

        Args:
            query: The user's query (not included in context — handled by caller).
            retrieval_results: Retrieved code chunks.
            project_summary: Short project profile.
            preferences: User preference string.
            memory: Relevant memory notes.
            conversation: Recent conversation summary.

        Returns:
            Formatted context string.
        """
        parts = ContextParts(
            retrieved_chunks=retrieval_results,
            project_summary=project_summary,
            user_preferences=preferences,
            memory_notes=memory,
            conversation_summary=conversation,
        )
        return self.build(parts)

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _format_chunks(
        chunks: list[RetrievalResult], budget: int
    ) -> str:
        """Format retrieval results into a context section within budget."""
        lines: list[str] = ["[Retrieved Code]"]
        used = len(lines[0])

        for i, chunk in enumerate(chunks):
            formatted = chunk.to_context_string()
            entry = f"\n---\n{formatted}"
            if used + len(entry) > budget:
                # Try to fit a truncated version
                available = budget - used - 10
                if available > 200:
                    truncated_entry = f"\n---\n{formatted[:available]}…"
                    lines.append(truncated_entry)
                break
            lines.append(entry)
            used += len(entry)

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count estimation (4 chars ≈ 1 token)."""
        return len(text) // CHARS_PER_TOKEN
