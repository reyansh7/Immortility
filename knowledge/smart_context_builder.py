"""Phase 3 Smart Context Builder — structured, budget-capped prompts for Qwen 8B."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from rag.retriever import RetrievalResult

logger = logging.getLogger(__name__)

# Effective reasoning budget for assembled context (chars ≈ tokens * 4)
DEFAULT_MAX_CHARS = 28_000  # ~7k tokens — inside 6k–8k reasoning band
MAX_SEMANTIC_CHUNKS = 4
MAX_CHUNK_CHARS = 900
MAX_GRAPH_CHARS = 3500
MAX_DIFF_CHARS = 2500


class SmartContextBuilder:
    """Build prompts with strict section order and hard size caps."""

    SECTION_ORDER = (
        "Project Facts & Architecture",
        "Repository Summary",
        "Relevant Graph Symbols & Imports",
        "Semantic Code Chunks",
        "Current File State & Recent Diff",
        "User Task",
    )

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.max_chars = max_chars

    def build(
        self,
        *,
        user_task: str,
        project_facts: list[dict[str, str]] | None = None,
        repo_summary: str = "",
        graph_hits: list[dict[str, Any]] | None = None,
        semantic_chunks: list[RetrievalResult] | None = None,
        current_file_state: str = "",
        recent_diff: str = "",
    ) -> str:
        facts_block = self._format_facts(project_facts or [])
        graph_block = self._format_graph(graph_hits or [])
        chunks_block = self._format_chunks(semantic_chunks or [])
        file_block = self._format_file_and_diff(current_file_state, recent_diff)

        sections = [
            ("Project Facts & Architecture", facts_block),
            ("Repository Summary", (repo_summary or "").strip()),
            ("Relevant Graph Symbols & Imports", graph_block),
            ("Semantic Code Chunks", chunks_block),
            ("Current File State & Recent Diff", file_block),
            ("User Task", (user_task or "").strip()),
        ]

        parts: list[str] = []
        used = 0
        for title, body in sections:
            body = (body or "").strip() or "(none)"
            block = f"## [{title}]\n{body}"
            # Always reserve room for User Task
            if title != "User Task" and used + len(block) + 500 > self.max_chars:
                remaining = max(200, self.max_chars - used - 500)
                block = f"## [{title}]\n{body[:remaining]}…"
            if used + len(block) > self.max_chars and title != "User Task":
                continue
            parts.append(block)
            used += len(block) + 2

        prompt = "\n\n".join(parts)
        if len(prompt) > self.max_chars:
            prompt = prompt[: self.max_chars - 1] + "…"
        logger.info("SmartContextBuilder: %d chars across %d sections", len(prompt), len(parts))
        return prompt

    @staticmethod
    def extract_symbol_candidates(query: str) -> list[str]:
        """Pull likely symbol / file tokens from a natural-language task."""
        candidates: list[str] = []
        # CamelCase / snake_case identifiers
        for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", query or ""):
            tok = m.group(0)
            low = tok.lower()
            if low in {
                "the", "and", "for", "with", "from", "this", "that", "file",
                "function", "class", "method", "please", "fix", "add", "update",
                "implement", "create", "read", "write", "project", "code",
            }:
                continue
            candidates.append(tok)
        # Paths like auth.py / login.tsx
        for m in re.finditer(r"\b[\w./\\-]+\.(?:py|ts|tsx|js|jsx)\b", query or ""):
            candidates.append(Path(m.group(0)).stem)
        # Dedupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out[:12]

    @staticmethod
    def _format_facts(facts: list[dict[str, str]]) -> str:
        if not facts:
            return ""
        return "\n".join(f"- {f.get('key')}: {f.get('value')}" for f in facts[:30])

    @staticmethod
    def _format_graph(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return ""
        lines: list[str] = []
        for h in hits[:8]:
            path = h.get("file_path", "")
            # Prefer relative-looking tail; never dump whole dependent files
            short = Path(path).name if path else "?"
            lines.append(
                f"- {h.get('kind', 'symbol')} `{h.get('symbol')}` @ {short}"
                f" (L{h.get('start_line', '?')}-{h.get('end_line', '?')})"
            )
            deps = h.get("dependencies") or []
            if deps:
                mods = sorted(
                    {
                        (d.get("source_module") or d.get("imported_name") or "").strip()
                        for d in deps
                    }
                    - {""}
                )[:8]
                if mods:
                    lines.append(f"  imports: {', '.join(mods)}")
            callees = h.get("callees") or []
            if callees:
                lines.append(f"  calls: {', '.join(callees[:8])}")
            callers = h.get("callers") or []
            if callers:
                caller_names = [
                    f"{c.get('caller')}@{Path(c.get('caller_file','')).name}"
                    for c in callers[:6]
                ]
                lines.append(f"  called_by: {', '.join(caller_names)}")
        text = "\n".join(lines)
        return text[:MAX_GRAPH_CHARS]

    @staticmethod
    def _format_chunks(chunks: list[RetrievalResult]) -> str:
        if not chunks:
            return ""
        blocks: list[str] = []
        for i, c in enumerate(chunks[:MAX_SEMANTIC_CHUNKS], 1):
            header = f"### chunk {i}: {c.filename}"
            if c.function_name:
                header += f" :: {c.function_name}"
            body = (c.content or "")[:MAX_CHUNK_CHARS]
            blocks.append(f"{header}\n{body}")
        return "\n\n".join(blocks)

    @staticmethod
    def _format_file_and_diff(file_state: str, diff: str) -> str:
        parts: list[str] = []
        if file_state.strip():
            parts.append("### Current file\n" + file_state.strip()[:MAX_DIFF_CHARS])
        if diff.strip():
            parts.append("### Recent diff\n" + diff.strip()[:MAX_DIFF_CHARS])
        return "\n\n".join(parts)
