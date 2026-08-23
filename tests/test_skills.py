"""On-demand skills and durable rules. Native Immortility, not vendored ECC."""

from __future__ import annotations

from skills.registry import (
    get_skill_registry,
    reset_skill_registry,
)


def setup_function() -> None:
    reset_skill_registry()


def test_always_skills_match_any_request():
    names = {s.name for s in get_skill_registry().match("hello")}
    assert "git_safety" in names
    assert "testing" in names
    assert "smallest_change" in names
    assert "safety" in names


def test_keyword_skills_match_python_and_inspect():
    names = {s.name for s in get_skill_registry().match("implement a python fix")}
    assert "python_edit" in names
    assert "inspect_first" in names
    assert "plan_first" in names


def test_prompt_block_loads_rule_files():
    block = get_skill_registry().prompt_block("fix a python bug")
    assert "### Skill:" in block
    assert "smallest" in block.lower() or "Smallest" in block
    assert "destructive" in block.lower() or "git" in block.lower()
