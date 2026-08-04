"""Project name resolution from natural language."""

from __future__ import annotations

from core.project_resolve import resolve_project_from_query


def test_resolve_stocks_app():
    cands = ["stocks_app", "SkillLens", "Portfolio", "Ai-Honeypot"]
    assert resolve_project_from_query("tell me about stocks app", cands) == "stocks_app"
    assert resolve_project_from_query("what does stocks_app do?", cands) == "stocks_app"
    assert resolve_project_from_query("explain SkillLens", cands) == "SkillLens"


def test_resolve_no_false_positive():
    cands = ["stocks_app", "SkillLens"]
    assert resolve_project_from_query("what is the weather today", cands) is None
