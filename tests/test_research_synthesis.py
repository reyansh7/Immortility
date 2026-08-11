"""Phase 2B — research synthesis citations."""

from __future__ import annotations

from agents.research_synth import ResearchSource, synthesize_with_citations


def test_research_synthesis_citations_present():
    sources = [
        ResearchSource(title="A", url="https://example.com/a", snippet="alpha"),
        ResearchSource(title="B", url="https://example.com/b", text="beta facts"),
    ]
    out = synthesize_with_citations(
        "explain alpha",
        sources,
        llm_answer="Alpha is explained here.",
    )
    assert "https://example.com/a" in out.answer or "https://example.com/a" in out.citations
    assert out.invented is False


def test_research_synthesis_empty_sources_no_hallucination():
    out = synthesize_with_citations("current bitcoin price", [])
    assert out.used_sources == 0
    low = out.answer.lower()
    assert "invent" in low or "could not find" in low
