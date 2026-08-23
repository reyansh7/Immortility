"""LLM planner is bounded and falls back to infer_plan."""

from __future__ import annotations

from pathlib import Path

from core.coding_engine import infer_plan
from core.coding_planner import (
    needs_llm_planner,
    plan_coding_task,
    plan_from_llm_payload,
    try_llm_plan,
)


def test_needs_llm_planner_skips_when_paths_are_known():
    heuristic = infer_plan("fix core/foo.py and run tests/test_foo.py")
    assert not needs_llm_planner("fix core/foo.py and run tests/test_foo.py", heuristic)


def test_needs_llm_planner_true_for_underspecified_feature():
    request = (
        "implement a multi-file authentication feature across the backend and "
        "frontend with a migration plan and risk notes for the existing session flow, "
        "including how login, tokens, and existing sessions should change"
    )
    heuristic = infer_plan(request)
    assert needs_llm_planner(request, heuristic)


def test_plan_from_llm_payload_keeps_paths_and_checks():
    fallback = infer_plan("do something")
    plan = plan_from_llm_payload(
        {
            "goal": "fix foo",
            "tasks": [{"id": "1", "title": "edit foo"}],
            "inspect_paths": ["core/foo.py"],
            "pytest_targets": ["tests/test_foo.py"],
        },
        "do something",
        fallback,
    )
    assert plan is not None
    assert "core/foo.py" in plan.inspect_paths
    assert "tests/test_foo.py" in plan.success_criteria.pytest_targets
    assert plan.success_criteria.is_explicit()


def test_plan_coding_task_uses_heuristic_without_model(tmp_path: Path, monkeypatch):
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("LLM planner should not run")

    monkeypatch.setattr("core.coding_planner.try_llm_plan", boom)
    plan = plan_coding_task("fix core/foo.py", tmp_path)
    assert plan.inspect_paths
    assert called["n"] == 0


def test_try_llm_plan_falls_back_on_cancel(tmp_path: Path, monkeypatch):
    class FakeKernel:
        def cancelled(self):
            return True

        def run_model(self, *_a, **_k):
            raise AssertionError("should not call model when cancelled")

    monkeypatch.setattr("core.execution_kernel.get_kernel", lambda: FakeKernel())
    assert try_llm_plan("implement a multi-file refactor of the planner", tmp_path) is None
