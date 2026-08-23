"""Deterministic coding hooks stay outside the LLM and respect cancel."""

from __future__ import annotations

from core.hooks import (
    BEFORE_EDIT,
    BEFORE_PLAN,
    HookResult,
    get_hook_runner,
    reset_hook_runner,
)
from core.harness import last_turn, reset_for_tests


def setup_function() -> None:
    reset_hook_runner()
    reset_for_tests()


def test_before_plan_injects_skill_block():
    result = get_hook_runner().run(BEFORE_PLAN, {"request": "fix python tests/test_foo.py"})
    assert result.ok
    assert result.extra_context
    assert "Skill:" in result.extra_context
    turn = last_turn()
    assert turn is not None
    assert turn.get("kind") == "coding"


def test_extra_handler_cannot_bypass_cancel(monkeypatch):
    monkeypatch.setattr("core.hooks._kernel_cancelled", lambda: True)

    def sneak(_payload):
        return HookResult(ok=True, extra_context="should not apply")

    runner = get_hook_runner()
    runner.add(BEFORE_EDIT, sneak)
    result = runner.run(BEFORE_EDIT, {"request": "edit core/foo.py"})
    assert result.cancelled
    assert not result.ok
    assert "should not apply" not in (result.extra_context or "")
