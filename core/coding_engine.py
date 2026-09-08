"""Phase 3 slice — autonomous coding loop over existing kernels.

Roles are labels on one loop, not new agents and not a second command engine.
Coder is ``execute_action`` (Tool Kernel + confirmations). Executor runs
allowlisted checks through ``CommandTool``. Reviewer uses read-only git
primitives. Destructive git is never issued here.
"""

from __future__ import annotations

import inspect
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from core.event_bus import EventBus
from core.harness import MODE_AGENT, TraceEvent, begin_turn, record
from core.pending_action import is_destructive_invocation, needs_confirmation
from core.repo_paths import get_repo_root

logger = logging.getLogger(__name__)

ROLE_PLANNER = "planner"
ROLE_CODER = "coder"
ROLE_EXECUTOR = "executor"
ROLE_DEBUGGER = "debugger"
ROLE_REVIEWER = "reviewer"
ROLE_REFLECTOR = "reflector"
ROLES = (
    ROLE_PLANNER,
    ROLE_CODER,
    ROLE_EXECUTOR,
    ROLE_DEBUGGER,
    ROLE_REVIEWER,
    ROLE_REFLECTOR,
)

STATUS_SUCCESS = "success"
STATUS_RETRY_EXHAUSTED = "retry_exhausted"
STATUS_CANCELLED = "cancelled"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_BLOCKED = "blocked"

_TEST_PATH_RE = re.compile(
    r"(?:(?<![.\w])(?:tests[/\\][\w./\\-]+\.py)|(?<![.\w])test_[\w-]+\.py)",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"(?<![.\w])(?:[\w.-]+[/\\])+[\w.-]+\.(?:py|ts|tsx|js|jsx|md)",
    re.IGNORECASE,
)

_CHECK_BLOCKS = (
    "git",
    "docker",
    "rm",
    "del",
    "rd",
    "format",
    "shutdown",
    "reboot",
)


@dataclass
class CodingTask:
    id: str
    title: str


@dataclass
class SuccessCriteria:
    """Every coding task must declare how we know it is done."""

    pytest_targets: list[str] = field(default_factory=list)
    py_compile: list[str] = field(default_factory=list)
    py_compile_touched: bool = True
    notes: str = ""

    def is_explicit(self) -> bool:
        return bool(self.pytest_targets or self.py_compile or self.py_compile_touched)


@dataclass
class CodingPlan:
    goal: str
    tasks: list[CodingTask]
    success_criteria: SuccessCriteria
    inspect_paths: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    ok: bool
    output: str = ""
    argv: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    ok: bool
    summary: str = ""
    changed_paths: list[str] = field(default_factory=list)
    patch: str = ""
    verdict: str = "PASS"
    findings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.ok and self.verdict == "PASS":
            self.verdict = "NEEDS_CHANGES"


@dataclass
class CodingLoopResult:
    status: str
    message: str
    role: str
    attempts: int = 0
    plan: CodingPlan | None = None
    check_output: str = ""
    changed_paths: list[str] = field(default_factory=list)
    planner_calls: int = 0
    coder_calls: int = 0
    reviewer_calls: int = 0
    retries: int = 0
    verified: bool = False
    latency_ms: int = 0
    planner_source: str = ""
    review_verdict: str = ""


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _retry_budget() -> int:
    try:
        from core.config import get_config

        n = int(get_config().decision_max_retries)
    except Exception:
        n = 3
    return max(1, min(n, 8))


def _resolve_cwd(explicit: str | Path | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    try:
        from tools.command_tool import resolve_project_cwd

        found = resolve_project_cwd()
        if found:
            return Path(found).resolve()
    except Exception:
        pass
    return get_repo_root().resolve()


def infer_plan(request: str, cwd: str | Path | None = None) -> CodingPlan:
    """Deterministic planner: verifiable tasks + success criteria, no LLM required."""
    text = (request or "").strip()
    root = _resolve_cwd(cwd)
    pytest_targets: list[str] = []
    for match in _TEST_PATH_RE.findall(text):
        rel = _safe_relpath(match, root)
        if rel and rel not in pytest_targets:
            pytest_targets.append(rel)
    inspect_paths: list[str] = []
    for match in _FILE_PATH_RE.findall(text):
        rel = _safe_relpath(match, root)
        if rel and rel not in inspect_paths:
            inspect_paths.append(rel)
    tasks = [CodingTask(id="1", title=text[:240] or "implement request")]
    notes = "Inspect before modify. Smallest reasonable change. Then run checks."
    criteria = SuccessCriteria(
        pytest_targets=pytest_targets,
        py_compile_touched=True,
        notes=notes,
    )
    return CodingPlan(
        goal=text or "coding task",
        tasks=tasks,
        success_criteria=criteria,
        inspect_paths=inspect_paths,
    )


def _safe_relpath(raw: str, root: Path) -> str | None:
    cleaned = (raw or "").replace("\\", "/").strip().strip("'\"")
    if not cleaned or cleaned.startswith("/") or ":" in cleaned[:3]:
        # Windows drive paths are resolved against root only if they stay inside it.
        try:
            abs_path = Path(cleaned).expanduser()
            if abs_path.is_absolute():
                resolved = abs_path.resolve()
                resolved.relative_to(root)
                return str(resolved.relative_to(root)).replace("\\", "/")
        except Exception:
            return None
        return None
    if ".." in Path(cleaned).parts:
        return None
    return cleaned.lstrip("./")


def allowlisted_check_argv(argv: Sequence[str], cwd: str | Path) -> list[str] | None:
    """Return a safe argv for pytest/py_compile, or None if the command is rejected."""
    args = [str(a) for a in argv if str(a).strip()]
    if len(args) < 3:
        return None
    head = [a.lower() for a in args[:3]]
    exe = Path(args[0]).name.lower()
    pythonish = exe in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
    if not pythonish and args[0] != sys.executable:
        return None
    if head[1] != "-m" or head[2] not in {"pytest", "py_compile"}:
        return None
    if args[0].lower() in _CHECK_BLOCKS or any(
        a.lower() in _CHECK_BLOCKS for a in args[1:3]
    ):
        return None
    try:
        from tools.command_tool import CLASS_DESTRUCTIVE, classify_argv

        if classify_argv(args) == CLASS_DESTRUCTIVE:
            return None
    except Exception:
        pass
    root = Path(cwd).resolve()
    safe = [sys.executable if pythonish or args[0] == sys.executable else args[0], "-m", args[2]]
    if args[2] == "pytest":
        safe.append("-q")
    for target in args[3:]:
        if target.startswith("-") and args[2] == "pytest":
            # Only harmless pytest selectors; never pass-through arbitrary flags.
            if target in {"-q", "-k"} or target.startswith("-k"):
                safe.append(target)
                continue
            return None
        rel = _safe_relpath(target, root)
        if not rel:
            return None
        safe.append(rel)
    display = " ".join(safe)
    if needs_confirmation("run_command", {"cmd": display}) and is_destructive_invocation(
        "run_command", {"cmd": display}
    ):
        return None
    return safe


def build_check_argvs(plan: CodingPlan, cwd: str | Path, touched: Sequence[str] = ()) -> list[list[str]]:
    """Compose allowlisted check argv lists from success criteria."""
    root = Path(cwd)
    out: list[list[str]] = []
    crit = plan.success_criteria
    if crit.pytest_targets:
        argv = allowlisted_check_argv(
            [sys.executable, "-m", "pytest", *crit.pytest_targets], root
        )
        if argv:
            out.append(argv)
    compile_targets = list(crit.py_compile)
    if crit.py_compile_touched:
        for path in touched:
            if str(path).endswith(".py"):
                rel = _safe_relpath(str(path), root) or str(path)
                if rel.endswith(".py") and rel not in compile_targets:
                    compile_targets.append(rel)
    if compile_targets:
        argv = allowlisted_check_argv(
            [sys.executable, "-m", "py_compile", *compile_targets], root
        )
        if argv:
            out.append(argv)
    return out


def _trace(role: str, *, detail: str = "", error: str = "", retries: int = 0) -> None:
    record(
        TraceEvent(
            kind="coding",
            tool=role,
            detail=(detail or role)[:300],
            error=error[:300],
            retries=retries,
            mode=MODE_AGENT,
        )
    )
    EventBus().publish("coding.role", {"role": role, "detail": detail, "error": error})


def _pending_confirmation() -> bool:
    try:
        from core.agent_state import AgentState

        return bool(AgentState().pending_action)
    except Exception:
        return False


def _kernel_cancelled() -> bool:
    try:
        from core.execution_kernel import get_kernel

        return get_kernel().cancelled()
    except Exception:
        return False


def _coder_prompt(plan: CodingPlan, debug_notes: str = "") -> str:
    tasks = "; ".join(t.title for t in plan.tasks) or plan.goal
    crit = plan.success_criteria
    checks = []
    if crit.pytest_targets:
        checks.append("pytest " + " ".join(crit.pytest_targets))
    if crit.py_compile_touched or crit.py_compile:
        checks.append("syntax-check touched Python files")
    check_line = "; ".join(checks) or "syntax-check touched files"
    inspect = ", ".join(plan.inspect_paths) or "relevant existing files"
    extra = f"\nPrevious check failure:\n{debug_notes[:4000]}" if debug_notes else ""
    return (
        f"Role: {ROLE_CODER}\n"
        f"Goal: {plan.goal}\n"
        f"Tasks: {tasks}\n"
        f"Inspect first: {inspect}\n"
        f"Success criteria: {check_line}. {crit.notes}\n"
        "Use discover_tools if you need a primitive. Read before edit. "
        "Make the smallest reasonable change. "
        "Never git reset --hard, force-push, branch-delete, or stash drop.\n"
        f"{extra}"
    )


async def _default_inspect(cwd: Path) -> None:
    from core.execution_kernel import get_kernel

    kernel = get_kernel()
    await kernel.run_tool("discover_tools", {"query": "edit git test"}, use_cache=False)
    await kernel.run_tool("git_status", {"cwd": str(cwd)}, use_cache=False)


async def _default_coder(
    prompt: str, *, context_override: str, auto_confirm: bool
) -> str:
    from core.action_engine import execute_action

    return await execute_action(
        prompt,
        require_edits=True,
        auto_confirm=auto_confirm,
        context_override=context_override,
    )


async def _default_executor(argvs: list[list[str]], cwd: Path) -> CheckResult:
    from tools.command_tool import CommandTool

    if not argvs:
        return CheckResult(ok=True, output="no checks configured")
    chunks: list[str] = []
    last_argv: list[str] = []
    for argv in argvs:
        safe = allowlisted_check_argv(argv, cwd)
        if not safe:
            return CheckResult(
                ok=False,
                output=f"rejected check argv: {argv}",
                argv=list(argv),
            )
        last_argv = safe
        raw = CommandTool.run_argv(safe, cwd=str(cwd), timeout=120.0)
        stdout = str(raw.get("stdout") or "")
        stderr = str(raw.get("stderr") or "")
        code = raw.get("returncode")
        piece = (stdout + "\n" + stderr).strip()
        chunks.append(piece or str(raw.get("message") or ""))
        if raw.get("cancelled") or raw.get("timed_out"):
            return CheckResult(ok=False, output="\n".join(chunks)[-8000:], argv=safe)
        if raw.get("status") == "error" or (code not in (0, None)):
            return CheckResult(ok=False, output="\n".join(chunks)[-8000:], argv=safe)
    return CheckResult(ok=True, output="\n".join(chunks)[-8000:], argv=last_argv)


async def _default_reviewer(cwd: Path) -> ReviewResult:
    from tools.git_tool import GitTool

    status = GitTool.status(cwd=str(cwd))
    diff = GitTool.diff(cwd=str(cwd))
    files = []
    if isinstance(status, dict):
        for row in status.get("files") or []:
            if isinstance(row, dict) and row.get("path"):
                files.append(str(row["path"]))
            elif isinstance(row, str):
                files.append(row)
    patch = ""
    if isinstance(diff, dict):
        patch = str(diff.get("patch") or "")
        for item in diff.get("files") or []:
            if item not in files:
                files.append(str(item))
    ok = True
    if isinstance(status, dict) and status.get("status") == "error":
        ok = False
    summary = (
        f"branch={status.get('branch') if isinstance(status, dict) else '?'} "
        f"changed={len(files)}"
    )
    return ReviewResult(
        ok=ok,
        summary=summary,
        changed_paths=files,
        patch=patch[:12000],
        verdict="PASS" if ok else "FAIL",
        findings=[] if ok else [summary],
    )


_COMPLEX_HINTS = ("multi-file", "feature", "refactor", "architecture")


def is_simple_coding_task(request: str, plan: CodingPlan | None = None) -> bool:
    """True for short, named-file edits that should skip a second inspect/LLM plan."""
    text = (request or "").lower()
    if any(k in text for k in _COMPLEX_HINTS):
        return False
    if len(request or "") > 220:
        return False
    if plan is not None and len(plan.inspect_paths) > 2:
        return False
    return True


async def _light_inspect(cwd: Path) -> None:
    from core.execution_kernel import get_kernel

    await get_kernel().run_tool("git_status", {"cwd": str(cwd)}, use_cache=True)


def _production_planner(request: str, cwd: Path) -> CodingPlan:
    from core.coding_planner import plan_coding_task

    return plan_coding_task(request, cwd)


def reflect(
    *,
    check: CheckResult,
    review: ReviewResult | None,
    attempt: int,
    max_retries: int,
    cancelled: bool = False,
    needs_confirm: bool = False,
) -> str:
    """Reflector: continue, retry, or finish. Pure function for tests."""
    if cancelled:
        return STATUS_CANCELLED
    if needs_confirm:
        return STATUS_NEEDS_CONFIRMATION
    review_ok = True if review is None else review.ok
    if check.ok and review_ok:
        return STATUS_SUCCESS
    if attempt >= max_retries:
        return STATUS_RETRY_EXHAUSTED
    return "retry"


async def run_coding_loop(
    request: str,
    *,
    cwd: str | Path | None = None,
    context_override: str = "",
    auto_confirm: bool = False,
    max_retries: int | None = None,
    planner: Callable[[str, Path], CodingPlan] | None = None,
    inspect: Callable[[Path], Awaitable[None] | None] | None = None,
    coder: Callable[..., Awaitable[str] | str] | None = None,
    executor: Callable[..., Awaitable[CheckResult] | CheckResult] | None = None,
    reviewer: Callable[[Path], Awaitable[ReviewResult] | ReviewResult] | None = None,
) -> CodingLoopResult:
    """Planner → inspect → Coder → Reviewer → Executor → Debugger → Reflector.

    Injected planner/inspect/coder/executor/reviewer still win (tests). Default
    planner/reviewer are lazy-imported so this module does not import them at
    load time. Destructive git is never issued here.
    """
    from core.hooks import (
        AFTER_EXECUTE,
        AFTER_REVIEW,
        BEFORE_EDIT,
        BEFORE_EXECUTE,
        BEFORE_FINALIZE,
        BEFORE_INSPECT,
        BEFORE_PLAN,
        BEFORE_REVIEW,
        get_hook_runner,
    )

    started = time.perf_counter()
    begin_turn(mode=MODE_AGENT)
    root = _resolve_cwd(cwd)
    budget = max_retries if max_retries is not None else _retry_budget()
    budget = max(1, int(budget))
    hooks = get_hook_runner()

    planner_calls = 0
    coder_calls = 0
    reviewer_calls = 0
    planner_source = "injected" if planner is not None else "plan_coding_task"
    last_check = CheckResult(ok=False, output="not run")
    last_review: ReviewResult | None = None
    last_coder = ""
    attempts = 0
    plan: CodingPlan | None = None

    def _finish(
        status: str,
        message: str,
        role: str,
        *,
        verified: bool = False,
    ) -> CodingLoopResult:
        return CodingLoopResult(
            status=status,
            message=message,
            role=role,
            attempts=attempts,
            plan=plan,
            check_output=last_check.output,
            changed_paths=list(last_review.changed_paths) if last_review else [],
            planner_calls=planner_calls,
            coder_calls=coder_calls,
            reviewer_calls=reviewer_calls,
            retries=max(0, attempts - 1) if attempts else 0,
            verified=verified,
            latency_ms=int((time.perf_counter() - started) * 1000),
            planner_source=planner_source,
            review_verdict=last_review.verdict if last_review else "",
        )

    try:
        from core.permissions import MODE_SAFE, get_mode

        if get_mode() == MODE_SAFE:
            return _finish(
                STATUS_BLOCKED,
                "Blocked in SAFE mode: coding edits are denied — /mode assisted to continue.",
                ROLE_PLANNER,
            )
    except Exception:
        pass

    if _kernel_cancelled():
        return _finish(STATUS_CANCELLED, "Cancelled before plan.", ROLE_PLANNER)

    pre_plan = hooks.run(BEFORE_PLAN, {"request": request, "cwd": str(root)})
    if pre_plan.cancelled or _kernel_cancelled():
        return _finish(STATUS_CANCELLED, "Cancelled before plan.", ROLE_PLANNER)

    plan_fn = planner if planner is not None else _production_planner
    plan = plan_fn(request, root)
    planner_calls = 1
    if pre_plan.extra_context:
        plan.success_criteria.notes = (
            f"{plan.success_criteria.notes}\n{pre_plan.extra_context}".strip()[:4000]
        )
    if not isinstance(plan, CodingPlan) or not plan.success_criteria.is_explicit():
        return _finish(
            STATUS_BLOCKED,
            "Planner did not produce success criteria.",
            ROLE_PLANNER,
        )
    _trace(ROLE_PLANNER, detail=plan.goal[:200])

    if _kernel_cancelled():
        return _finish(STATUS_CANCELLED, "Cancelled before inspect.", ROLE_PLANNER)

    simple = is_simple_coding_task(request, plan)
    pre_inspect = hooks.run(
        BEFORE_INSPECT,
        {"request": request, "simple": simple, "cwd": str(root)},
    )
    if pre_inspect.cancelled or _kernel_cancelled():
        return _finish(STATUS_CANCELLED, "Cancelled before inspect.", ROLE_PLANNER)
    if inspect is not None:
        inspect_fn = inspect
    elif pre_inspect.skip:
        inspect_fn = _light_inspect
    else:
        inspect_fn = _default_inspect
    try:
        await _maybe_await(inspect_fn(root))
    except Exception as exc:
        logger.debug("coding inspect: %s", exc)
    _trace(ROLE_PLANNER, detail="inspect")

    debug_notes = ""
    coder_fn = coder if coder is not None else _default_coder
    executor_fn = executor if executor is not None else _default_executor

    for attempt in range(1, budget + 1):
        attempts = attempt
        if _kernel_cancelled():
            return _finish(STATUS_CANCELLED, "Cancelled.", ROLE_CODER)

        hooks.run(BEFORE_EDIT, {"cwd": str(root), "attempt": attempt})
        role = ROLE_DEBUGGER if debug_notes else ROLE_CODER
        prompt = _coder_prompt(plan, debug_notes)
        last_coder = str(
            await _maybe_await(
                coder_fn(
                    prompt,
                    context_override=context_override,
                    auto_confirm=auto_confirm,
                )
            )
        )
        coder_calls += 1
        _trace(role, detail=f"attempt {attempt}", retries=attempt - 1)

        if _pending_confirmation():
            _trace(ROLE_REFLECTOR, detail=STATUS_NEEDS_CONFIRMATION)
            return _finish(STATUS_NEEDS_CONFIRMATION, last_coder, ROLE_CODER)

        if _kernel_cancelled():
            return _finish(STATUS_CANCELLED, "Cancelled.", ROLE_CODER)

        async def _fresh_reviewer(review_cwd: Path) -> ReviewResult:
            from core.coding_reviewer import review_coding_result

            check_for_review = last_check if last_check.output != "not run" else None
            return review_coding_result(
                review_cwd,
                plan,
                check_for_review,
                use_llm=not simple,
            )

        hooks.run(
            BEFORE_REVIEW,
            {
                "goal": plan.goal,
                "cwd": str(root),
                "changed_paths": list(last_review.changed_paths) if last_review else [],
            },
        )
        reviewer_fn = reviewer if reviewer is not None else _fresh_reviewer
        last_review = await _maybe_await(reviewer_fn(root))
        reviewer_calls += 1
        hooks.run(
            AFTER_REVIEW,
            {
                "ok": last_review.ok,
                "findings": list(last_review.findings),
            },
        )
        _trace(ROLE_REVIEWER, detail=last_review.summary)

        if not last_review.ok:
            findings = "\n".join(last_review.findings)
            last_check = CheckResult(
                ok=False,
                output=(
                    f"{last_review.summary}\n{findings}".strip()
                    if (last_review.summary or findings)
                    else "review rejected"
                ),
            )
            decision = reflect(
                check=last_check,
                review=last_review,
                attempt=attempt,
                max_retries=budget,
                cancelled=_kernel_cancelled(),
                needs_confirm=_pending_confirmation(),
            )
            _trace(ROLE_REFLECTOR, detail=decision, retries=attempt - 1)
            if decision == STATUS_CANCELLED:
                return _finish(STATUS_CANCELLED, "Cancelled during review.", ROLE_REFLECTOR)
            if decision != "retry":
                break
            debug_notes = last_check.output or "review rejected"
            _trace(ROLE_DEBUGGER, detail=debug_notes[:200], retries=attempt)
            continue

        argvs = build_check_argvs(plan, root, last_review.changed_paths)
        pre_exec = hooks.run(
            BEFORE_EXECUTE,
            {"argv": argvs[0] if argvs else [], "cwd": str(root)},
        )
        if pre_exec.cancelled or _kernel_cancelled():
            return _finish(STATUS_CANCELLED, "Cancelled before checks.", ROLE_EXECUTOR)
        last_check = await _maybe_await(executor_fn(argvs, root))
        hooks.run(AFTER_EXECUTE, {"ok": last_check.ok})
        _trace(
            ROLE_EXECUTOR,
            detail=" ".join(last_check.argv)[:200],
            error="" if last_check.ok else last_check.output[:200],
        )

        decision = reflect(
            check=last_check,
            review=last_review,
            attempt=attempt,
            max_retries=budget,
            cancelled=_kernel_cancelled(),
            needs_confirm=_pending_confirmation(),
        )
        _trace(ROLE_REFLECTOR, detail=decision, retries=attempt - 1)

        if decision == STATUS_SUCCESS:
            hooks.run(
                BEFORE_FINALIZE,
                {
                    "check_ok": last_check.ok,
                    "review_ok": last_review.ok if last_review else True,
                },
            )
            summary = last_coder.strip() or "Coding loop finished."
            extra = last_review.summary if last_review else ""
            msg = summary if not extra else f"{summary}\n\nReview: {extra}"
            return _finish(STATUS_SUCCESS, msg, ROLE_REFLECTOR, verified=True)
        if decision == STATUS_CANCELLED:
            return _finish(STATUS_CANCELLED, "Cancelled during checks.", ROLE_REFLECTOR)
        if decision != "retry":
            break
        debug_notes = last_check.output or "checks failed"
        _trace(ROLE_DEBUGGER, detail=debug_notes[:200], retries=attempt)

    fail_msg = (
        f"Coding loop stopped after {attempts} attempt(s). "
        f"Checks still failing.\n{last_check.output[-2000:]}"
    )
    return _finish(STATUS_RETRY_EXHAUSTED, fail_msg, ROLE_REFLECTOR)


def wants_coding_loop(message: str, *, require_edits: bool = False) -> bool:
    """True when the autonomous coding loop should wrap the Action Engine."""
    if not require_edits:
        return False
    from core.intent import is_coding_intent

    return is_coding_intent(message)
