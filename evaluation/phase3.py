"""Small Phase 3 eval suite. Deterministic mocks — not the Phase 7 25-task harness."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.coding_engine import (
    STATUS_CANCELLED,
    STATUS_NEEDS_CONFIRMATION,
    STATUS_RETRY_EXHAUSTED,
    STATUS_SUCCESS,
    CheckResult,
    ReviewResult,
    infer_plan,
    run_coding_loop,
)
from core.harness import last_turn, reset_for_tests


@dataclass
class EvalCase:
    id: str
    request: str
    coder: Callable[..., Any]
    executor: Callable[..., Any]
    reviewer: Callable[..., Any]
    expect_status: str
    inspect: Callable[..., Any] | None = None
    planner: Callable[..., Any] | None = None
    cancel: bool = False
    confirm: bool = False
    max_retries: int = 3


@dataclass
class EvalResult:
    id: str
    ok: bool
    status: str
    attempts: int
    latency_ms: int
    traced: bool
    detail: str = ""


async def _run_case(case: EvalCase) -> EvalResult:
    reset_for_tests()
    if case.cancel:
        from core import coding_engine as ce

        orig = ce._kernel_cancelled
        ce._kernel_cancelled = lambda: True
    if case.confirm:
        from core import coding_engine as ce

        orig_p = ce._pending_confirmation
        ce._pending_confirmation = lambda: True
    started = time.perf_counter()
    try:
        result = await run_coding_loop(
            case.request,
            max_retries=case.max_retries,
            inspect=case.inspect if case.inspect is not None else (lambda _c: None),
            coder=case.coder,
            executor=case.executor,
            reviewer=case.reviewer,
            planner=case.planner or infer_plan,
        )
    finally:
        if case.cancel:
            ce._kernel_cancelled = orig
        if case.confirm:
            ce._pending_confirmation = orig_p
    latency = int((time.perf_counter() - started) * 1000)
    turn = last_turn()
    ok = result.status == case.expect_status
    return EvalResult(
        id=case.id,
        ok=ok,
        status=result.status,
        attempts=result.attempts,
        latency_ms=latency,
        traced=bool(turn and turn.get("kind") == "coding"),
        detail=result.message[:200],
    )


def _cases() -> list[EvalCase]:
    async def ok_coder(prompt, **_k):
        assert "Success criteria" in prompt
        return "edited"

    async def debug_coder(prompt, **_k):
        return "retried"

    async def ok_exec(_a, _c):
        return CheckResult(ok=True, output="pass")

    fail_then_pass = {"n": 0}

    async def flaky_exec(_a, _c):
        fail_then_pass["n"] += 1
        if fail_then_pass["n"] == 1:
            return CheckResult(ok=False, output="boom")
        return CheckResult(ok=True, output="pass")

    always_fail = {"n": 0}

    async def fail_exec(_a, _c):
        always_fail["n"] += 1
        return CheckResult(ok=False, output="still failing")

    async def ok_review(_c):
        return ReviewResult(ok=True, summary="ok", changed_paths=["a.py"])

    reject_then_pass = {"n": 0}

    async def flaky_review(_c):
        reject_then_pass["n"] += 1
        if reject_then_pass["n"] == 1:
            return ReviewResult(ok=False, summary="needs tests", findings=["add a test"])
        return ReviewResult(ok=True, summary="ok")

    return [
        EvalCase("simple_edit", "fix typo in core/foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("named_test", "fix core/foo.py and run tests/test_foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("check_retry", "fix core/foo.py", debug_coder, flaky_exec, ok_review, STATUS_SUCCESS),
        EvalCase("review_retry", "fix core/foo.py", debug_coder, ok_exec, flaky_review, STATUS_SUCCESS),
        EvalCase("retry_exhausted", "fix tests/test_x.py", debug_coder, fail_exec, ok_review, STATUS_RETRY_EXHAUSTED, max_retries=2),
        EvalCase("cancel", "implement foo", ok_coder, ok_exec, ok_review, STATUS_CANCELLED, cancel=True),
        EvalCase("confirm", "edit file x.py", ok_coder, ok_exec, ok_review, STATUS_NEEDS_CONFIRMATION, confirm=True),
        EvalCase("inspect_injected", "fix core/foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS, inspect=lambda _c: None),
        EvalCase("git_aware", "fix core/foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("tool_use_prompt", "fix core/foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("project_route", "add logging to detect.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("doc_stub", "update docs for core/foo.py", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("multi_file", "refactor core/foo.py and core/bar.py feature", ok_coder, ok_exec, ok_review, STATUS_SUCCESS),
        EvalCase("failing_tests", "fix tests/test_foo.py", debug_coder, fail_exec, ok_review, STATUS_RETRY_EXHAUSTED, max_retries=2),
        EvalCase("timeout_as_fail", "fix core/foo.py", debug_coder, fail_exec, ok_review, STATUS_RETRY_EXHAUSTED, max_retries=1),
    ]


async def run_phase3_eval() -> dict[str, Any]:
    """Run the mocked Phase 3 suite. Safe without a live model."""
    rows: list[EvalResult] = []
    for case in _cases():
        rows.append(await _run_case(case))
    passed = sum(1 for r in rows if r.ok)
    return {
        "passed": passed,
        "total": len(rows),
        "ok": passed == len(rows),
        "cases": [r.__dict__ for r in rows],
    }
