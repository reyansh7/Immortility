"""Synthesize multi-source web research with citations (fail-closed)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchSource:
    title: str
    url: str
    snippet: str = ""
    text: str = ""


@dataclass
class SynthesisResult:
    answer: str
    citations: list[str] = field(default_factory=list)
    used_sources: int = 0
    invented: bool = False


def synthesize_with_citations(
    query: str,
    sources: list[ResearchSource],
    *,
    llm_answer: str | None = None,
) -> SynthesisResult:
    """Build a cited answer. If no sources, refuse to invent current facts."""
    usable = [s for s in sources if (s.url or "").strip()]
    if not usable:
        return SynthesisResult(
            answer=(
                f"I could not find reliable web sources for: {query}. "
                "I will not invent current facts — try again later or rephrase."
            ),
            citations=[],
            used_sources=0,
            invented=False,
        )

    citations = [s.url for s in usable if s.url]
    # Prefer LLM synthesis when provided; otherwise deterministic summary
    if llm_answer and llm_answer.strip():
        answer = llm_answer.strip()
        # Ensure at least one citation appears
        if citations and not any(c in answer for c in citations):
            answer += "\n\nSources:\n" + "\n".join(f"- {u}" for u in citations[:8])
        return SynthesisResult(
            answer=answer,
            citations=citations[:8],
            used_sources=len(usable),
            invented=False,
        )

    lines = [f"Research findings for: {query}", ""]
    for i, s in enumerate(usable[:5], 1):
        snippet = (s.text or s.snippet or "").strip().replace("\n", " ")[:280]
        lines.append(f"{i}. {s.title or s.url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append(f"   Source: {s.url}")
        lines.append("")
    lines.append("Sources: " + ", ".join(citations[:8]))
    return SynthesisResult(
        answer="\n".join(lines).strip(),
        citations=citations[:8],
        used_sources=len(usable),
        invented=False,
    )


def needs_live_web(query: str) -> bool:
    """Delegate to source_router's stricter fresh/explicit web heuristics."""
    try:
        from core.source_router import needs_live_web as _nlw

        return _nlw(query)
    except Exception:
        low = (query or "").lower()
        cues = (
            "today", "latest", "current price", "news", "who won",
            "search the web", "look up", "research", "find online",
        )
        return any(c in low for c in cues)
