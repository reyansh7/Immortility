"""LLM-assisted coding planner with a deterministic fallback.

Does not execute tools, edit files, or run commands. One bounded model call;
invalid/timeout/unavailable → ``infer_plan``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.coding_engine import CodingPlan, CodingTask, SuccessCriteria, infer_plan
from core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

_PLANNER_TIMEOUT_S = 25.0
_MAX_TASKS = 8
_MAX_PATHS = 12

_PLANNER_SYSTEM = (
    "You are the Immortility coding planner. Return ONLY JSON. "
    "Do not write code, do not call tools, do not modify files. "
    "Do not include chain-of-thought. "
    "Schema: {"
    '"goal": str, '
    '"tasks": [{"id": str, "title": str}], '
    '"inspect_paths": [str], '
    '"required_tools": [str], '
    '"pytest_targets": [str], '
    '"py_compile": [str], '
    '"py_compile_touched": bool, '
    '"notes": str, '
    '"risks": [str]'
    "} "
    "Keep the plan small. Prefer existing files. Never request destructive git."
)

_COMPLEX_HINTS = (
    "multi-file",
    "across",
    "refactor",
    "architecture",
    "feature",
    "implement",
    "migrate",
    "redesign",
)


def needs_llm_planner(request: str, heuristic: CodingPlan) -> bool:
    """True when the heuristic plan is under-specified for a larger change."""
    text = (request or "").lower()
    if heuristic.inspect_paths or heuristic.success_criteria.pytest_targets:
        return False
    if len(request or "") < 160:
        return False
    return any(k in text for k in _COMPLEX_HINTS) or len(request) >= 280


def _as_str_list(value: Any, *, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip().replace("\\", "/")
        if not text or text in out:
            continue
        if ".." in Path(text).parts:
            continue
        out.append(text)
        if len(out) >= cap:
            break
    return out


def plan_from_llm_payload(data: dict[str, Any], request: str, fallback: CodingPlan) -> CodingPlan | None:
    if not isinstance(data, dict):
        return None
    goal = str(data.get("goal") or request or fallback.goal).strip()[:500]
    tasks_raw = data.get("tasks") or []
    tasks: list[CodingTask] = []
    if isinstance(tasks_raw, list):
        for i, item in enumerate(tasks_raw[:_MAX_TASKS], start=1):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or "").strip()
                tid = str(item.get("id") or i)
            else:
                title = str(item).strip()
                tid = str(i)
            if title:
                tasks.append(CodingTask(id=tid, title=title[:240]))
    if not tasks:
        tasks = list(fallback.tasks) or [CodingTask(id="1", title=goal[:240] or "implement request")]
    inspect_paths = _as_str_list(data.get("inspect_paths") or data.get("files"), cap=_MAX_PATHS)
    if not inspect_paths:
        inspect_paths = list(fallback.inspect_paths)
    pytest_targets = _as_str_list(
        data.get("pytest_targets") or data.get("tests"), cap=_MAX_PATHS
    )
    if not pytest_targets:
        pytest_targets = list(fallback.success_criteria.pytest_targets)
    py_compile = _as_str_list(data.get("py_compile"), cap=_MAX_PATHS)
    touched = data.get("py_compile_touched")
    if not isinstance(touched, bool):
        touched = True
    notes = str(data.get("notes") or fallback.success_criteria.notes or "").strip()[:800]
    risks = _as_str_list(data.get("risks"), cap=6)
    if risks:
        notes = f"{notes} Risks: {'; '.join(risks)}".strip()
    tools = _as_str_list(data.get("required_tools"), cap=8)
    if tools:
        notes = f"{notes} Tools: {', '.join(tools)}".strip()
    criteria = SuccessCriteria(
        pytest_targets=pytest_targets,
        py_compile=py_compile,
        py_compile_touched=touched,
        notes=notes or fallback.success_criteria.notes,
    )
    if not criteria.is_explicit():
        return None
    return CodingPlan(goal=goal or fallback.goal, tasks=tasks, success_criteria=criteria, inspect_paths=inspect_paths)


def _model_text(value: Any) -> str:
    if isinstance(value, dict):
        msg = value.get("message") or {}
        if isinstance(msg, dict):
            return str(msg.get("content") or "")
        return str(value.get("content") or "")
    return str(value or "")


def try_llm_plan(request: str, cwd: Path) -> CodingPlan | None:
    """One bounded JSON model call. None means caller should use infer_plan."""
    try:
        from core.execution_kernel import get_kernel

        kernel = get_kernel()
        if kernel.cancelled():
            return None
        preview = ""
        try:
            from tools.git_tool import GitTool

            status = GitTool.status(cwd=str(cwd))
            if isinstance(status, dict):
                preview = f"branch={status.get('branch')} files={status.get('files')}"[:800]
        except Exception:
            preview = ""
        messages = [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {
                "role": "user",
                "content": f"Request:\n{request[:4000]}\nCwd: {cwd}\nRepo preview: {preview}",
            },
        ]
        result = kernel.run_model(
            messages,
            role="code",
            timeout_s=_PLANNER_TIMEOUT_S,
            format="json",
            temperature=0.1,
            max_output_tokens=700,
        )
        if not result.ok or result.cancelled:
            return None
        data = parse_llm_json(_model_text(result.value))
        if not isinstance(data, dict):
            return None
        fallback = infer_plan(request, cwd)
        return plan_from_llm_payload(data, request, fallback)
    except Exception as exc:
        logger.debug("llm planner fallback: %s", exc)
        return None


def plan_coding_task(request: str, cwd: str | Path) -> CodingPlan:
    """Production planner: heuristic first, LLM only when justified."""
    root = Path(cwd)
    heuristic = infer_plan(request, root)
    if not needs_llm_planner(request, heuristic):
        return heuristic
    llm_plan = try_llm_plan(request, root)
    if llm_plan is not None and llm_plan.success_criteria.is_explicit():
        return llm_plan
    return heuristic
