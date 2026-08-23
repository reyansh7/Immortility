"""Phase 3 slice — autonomous coding loop over existing kernels."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.coding_engine import (
    ROLE_CODER,
    ROLE_DEBUGGER,
    ROLE_EXECUTOR,
    ROLE_PLANNER,
    ROLE_REFLECTOR,
    ROLE_REVIEWER,
    ROLES,
    STATUS_NEEDS_CONFIRMATION,
    STATUS_RETRY_EXHAUSTED,
    STATUS_SUCCESS,
    CheckResult,
    CodingPlan,
    ReviewResult,
    SuccessCriteria,
    allowlisted_check_argv,
    build_check_argvs,
    infer_plan,
    reflect,
    run_coding_loop,
    wants_coding_loop,
)
from core.harness import last_turn, reset_for_tests
from core.pending_action import needs_confirmation


def test_roles_are_thin_labels():
    assert ROLES == (
        ROLE_PLANNER,
        ROLE_CODER,
        ROLE_EXECUTOR,
        ROLE_DEBUGGER,
        ROLE_REVIEWER,
        ROLE_REFLECTOR,
    )


def test_plan_always_has_success_criteria():
    plan = infer_plan("fix the bug in core/foo.py and run tests/test_foo.py")
    assert plan.goal
    assert plan.tasks
    assert plan.success_criteria.is_explicit()
    assert "tests/test_foo.py" in plan.success_criteria.pytest_targets
    assert "core/foo.py" in plan.inspect_paths


def test_executor_rejects_destructive_and_non_check_argv(tmp_path):
    cwd = tmp_path
    assert allowlisted_check_argv(["git", "reset", "--hard"], cwd) is None
    assert allowlisted_check_argv(["rm", "-rf", "/"], cwd) is None
    assert allowlisted_check_argv(["python", "-m", "pytest", "../escape.py"], cwd) is None
    safe = allowlisted_check_argv(["python", "-m", "pytest", "tests/test_foo.py"], cwd)
    assert safe is not None
    assert safe[1:3] == ["-m", "pytest"]
    assert "tests/test_foo.py" in safe


def test_build_check_argvs_compiles_touched_python(tmp_path):
    plan = infer_plan("implement a helper")
    argvs = build_check_argvs(plan, tmp_path, ["core/foo.py", "README.md"])
    joined = " ".join(" ".join(a) for a in argvs)
    assert "py_compile" in joined
    assert "core/foo.py" in joined


def test_reflect_retry_then_exhaust():
    fail = CheckResult(ok=False, output="boom")
    ok = CheckResult(ok=True, output="pass")
    review = ReviewResult(ok=True, summary="changed=1")
    assert reflect(check=fail, review=review, attempt=1, max_retries=2) == "retry"
    assert (
        reflect(check=fail, review=review, attempt=2, max_retries=2)
        == STATUS_RETRY_EXHAUSTED
    )
    assert reflect(check=ok, review=review, attempt=1, max_retries=2) == STATUS_SUCCESS


def test_coding_engine_source_reuses_command_tool():
    src = Path("core/coding_engine.py").read_text(encoding="utf-8")
    assert "import subprocess" not in src
    assert "Popen" not in src
    assert "CommandTool" in src
    assert "execute_action" in src
    assert "discover_tools" in src


def test_phase2b_confirmation_gates_unchanged():
    assert needs_confirmation("write_file", {"path": "x.py"})
    assert needs_confirmation("run_command", {"cmd": "pytest"})
    assert needs_confirmation("git_reset", {"mode": "hard"})
    assert not needs_confirmation("git_status", {"cwd": "."})


@pytest.mark.asyncio
async def test_loop_debug_retry_then_success(monkeypatch):
    reset_for_tests()
    calls = {"coder": 0, "exec": 0}

    async def inspect(_cwd):
        return None

    async def coder(prompt, **_kwargs):
        calls["coder"] += 1
        assert "Success criteria" in prompt
        if calls["coder"] == 1:
            assert "Previous check failure" not in prompt
        else:
            assert "Previous check failure" in prompt
            assert ROLE_DEBUGGER or "fail-1" in prompt
        return "patched helper"

    async def executor(argvs, _cwd):
        calls["exec"] += 1
        if calls["exec"] == 1:
            return CheckResult(ok=False, output="fail-1", argv=argvs[0] if argvs else [])
        return CheckResult(ok=True, output="pass", argv=argvs[0] if argvs else [])

    async def reviewer(_cwd):
        return ReviewResult(ok=True, summary="changed=1", changed_paths=["a.py"])

    result = await run_coding_loop(
        "fix a.py and run tests/test_a.py",
        cwd=".",
        max_retries=3,
        inspect=inspect,
        coder=coder,
        executor=executor,
        reviewer=reviewer,
    )
    assert result.status == STATUS_SUCCESS
    assert calls["coder"] == 2
    assert calls["exec"] == 2
    assert result.attempts == 2
    turn = last_turn()
    assert turn is not None
    assert turn.get("kind") == "coding"


@pytest.mark.asyncio
async def test_loop_stops_on_confirmation(monkeypatch):
    monkeypatch.setattr(
        "core.coding_engine._pending_confirmation", lambda: True
    )

    async def coder(prompt, **_kwargs):
        return "I am about to modify:\nx.py\nvia `write_file`.\nProceed? (yes/no)"

    result = await run_coding_loop(
        "edit file x.py",
        max_retries=3,
        inspect=lambda _c: None,
        coder=coder,
        executor=lambda *_a, **_k: CheckResult(ok=True),
        reviewer=lambda _c: ReviewResult(ok=True),
    )
    assert result.status == STATUS_NEEDS_CONFIRMATION
    assert result.attempts == 1
    assert "Proceed" in result.message


@pytest.mark.asyncio
async def test_loop_respects_cancel(monkeypatch):
    monkeypatch.setattr("core.coding_engine._kernel_cancelled", lambda: True)
    result = await run_coding_loop(
        "implement foo",
        inspect=lambda _c: None,
        coder=AsyncMock(return_value="nope"),
    )
    assert result.status == "cancelled"
    assert result.attempts == 0


@pytest.mark.asyncio
async def test_loop_exhausts_retry_budget():
    async def coder(prompt, **_kwargs):
        return "still broken"

    async def executor(_argvs, _cwd):
        return CheckResult(ok=False, output="still failing")

    result = await run_coding_loop(
        "fix tests/test_x.py",
        max_retries=2,
        inspect=lambda _c: None,
        coder=coder,
        executor=executor,
        reviewer=lambda _c: ReviewResult(ok=True, changed_paths=["x.py"]),
    )
    assert result.status == STATUS_RETRY_EXHAUSTED
    assert result.attempts == 2
    assert "still failing" in result.message


def test_wants_coding_loop_only_for_edits():
    assert wants_coding_loop("fix the login bug", require_edits=True)
    assert not wants_coding_loop("fix the login bug", require_edits=False)
    assert not wants_coding_loop("hello", require_edits=True)


def test_planner_rejects_empty_criteria():
    plan = CodingPlan(
        goal="x",
        tasks=[],
        success_criteria=SuccessCriteria(
            pytest_targets=[], py_compile=[], py_compile_touched=False
        ),
    )
    assert not plan.success_criteria.is_explicit()


@pytest.mark.asyncio
async def test_blocked_when_planner_omits_criteria():
    def bad_planner(request, cwd):
        return CodingPlan(
            goal=request,
            tasks=[],
            success_criteria=SuccessCriteria(py_compile_touched=False),
        )

    result = await run_coding_loop("do stuff", planner=bad_planner)
    assert result.status == "blocked"
    assert result.role == ROLE_PLANNER


@pytest.mark.asyncio
async def test_default_executor_uses_command_tool(monkeypatch, tmp_path):
    seen = {}

    def fake_run_argv(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["cwd"] = kwargs.get("cwd")
        return {
            "status": "success",
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "cancelled": False,
            "timed_out": False,
        }

    monkeypatch.setattr(
        "tools.command_tool.CommandTool.run_argv", staticmethod(fake_run_argv)
    )
    from core.coding_engine import _default_executor

    plan = infer_plan("run tests/test_foo.py")
    argvs = build_check_argvs(plan, tmp_path, [])
    result = await _default_executor(argvs, tmp_path)
    assert result.ok
    assert seen["argv"][1:3] == ["-m", "pytest"]
    assert seen["cwd"] == str(tmp_path)


def test_hud_and_cli_wire_the_coding_loop():
    hud = Path("tools/hud_agent.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "run_coding_loop" in hud
    assert "auto_confirm=False" in hud
    assert "run_coding_loop" in main
