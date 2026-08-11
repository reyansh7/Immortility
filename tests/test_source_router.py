"""Source router — WEB vs MEMORY vs LOCAL vs TOOLS."""

from __future__ import annotations

from agents.research_synth import ResearchSource, synthesize_with_citations
from core.source_router import choose_source, needs_live_web


def test_synthesize_fail_closed_without_sources():
    result = synthesize_with_citations("latest python release", [])
    assert result.used_sources == 0
    assert "will not invent" in result.answer.lower() or "could not find" in result.answer.lower()


def test_source_router_web_when_explicit_or_fresh():
    assert choose_source("research the latest Next.js release").source == "WEB"
    assert choose_source("look up who won the election today").source == "WEB"
    assert choose_source("what is the latest version of Next.js").source == "WEB"
    assert needs_live_web("current bitcoin price today") is True


def test_source_router_local_for_general_and_casual():
    assert choose_source("hi how are you").source == "LOCAL"
    # General knowledge — do NOT force web
    assert choose_source("what is a binary search tree").source == "LOCAL"
    assert choose_source("explain recursion simply").source == "LOCAL"


def test_source_router_memory_and_project():
    assert choose_source("what did we fix last time for jwt").source == "MEMORY"
    assert choose_source("summarize my stocks_app project").source in {"LOCAL", "MEMORY"}
    rich = "Learned memory:\n[learned:outcome] jwt oauth preference\n" + ("x" * 200)
    assert choose_source("how does auth work here", routing_context=rich).source == "MEMORY"


def test_source_router_tools():
    assert choose_source("fix the login bug in auth.py").source == "TOOLS"
