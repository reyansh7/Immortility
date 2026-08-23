"""Fresh-context coding reviewer. Read-only. Never reuses coder conversation.

Inspects git status/diff (and optional check output) on a new message list.
PASS / FAIL / NEEDS_CHANGES. LLM failure falls back to git-only review.
Destructive git is never issued here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.coding_engine import CheckResult, CodingPlan, ReviewResult
from core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

_REVIEW_TIMEOUT_S = 25.0
_VERDICT_PASS = "PASS"
_VERDICT_FAIL = "FAIL"
_VERDICT_NEEDS_CHANGES = "NEEDS_CHANGES"

_REVIEW_SYSTEM = (
    "You are an independent code reviewer. You did not write these changes. "
    "Return ONLY JSON. Do not edit files. Do not run git write commands. "
    "Do not include chain-of-thought. "
    "Schema: {"
    '"verdict": "PASS"|"FAIL"|"NEEDS_CHANGES", '
    '"summary": str, '
    '"findings": [str], '
    '"ok": bool'
    "} "
    "PASS only if the diff matches the requested goal, looks correct, "
    "does not introduce obvious regressions or unsafe git, and checks (if any) look healthy. "
    "NEEDS_CHANGES for fixable gaps. FAIL for wrong/unsafe/unrelated edits."
)


def git_review_packet(cwd: str | Path) -> ReviewResult:
    """Read-only git snapshot used by both LLM and fallback review."""
    from tools.git_tool import GitTool

    status = GitTool.status(cwd=str(cwd))
    diff = GitTool.diff(cwd=str(cwd))
    files: list[str] = []
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
        verdict=_VERDICT_PASS if ok else _VERDICT_FAIL,
        findings=[] if ok else [summary],
    )


def review_from_llm_payload(data: dict[str, Any], packet: ReviewResult) -> ReviewResult | None:
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").strip().upper().replace(" ", "_")
    if verdict in {"NEED_CHANGES", "NEEDS-CHANGES", "CHANGES"}:
        verdict = _VERDICT_NEEDS_CHANGES
    if verdict not in {_VERDICT_PASS, _VERDICT_FAIL, _VERDICT_NEEDS_CHANGES}:
        if "ok" in data:
            verdict = _VERDICT_PASS if data.get("ok") else _VERDICT_NEEDS_CHANGES
        else:
            return None
    findings_raw = data.get("findings") or []
    findings = [str(x).strip() for x in findings_raw if str(x).strip()][:12]
    summary = str(data.get("summary") or packet.summary).strip()[:800]
    ok = verdict == _VERDICT_PASS
    return ReviewResult(
        ok=ok,
        summary=summary,
        changed_paths=list(packet.changed_paths),
        patch=packet.patch,
        verdict=verdict,
        findings=findings,
    )


def _model_text(value: Any) -> str:
    if isinstance(value, dict):
        msg = value.get("message") or {}
        if isinstance(msg, dict):
            return str(msg.get("content") or "")
        return str(value.get("content") or "")
    return str(value or "")


def try_llm_review(
    cwd: str | Path,
    plan: CodingPlan | None = None,
    check: CheckResult | None = None,
    packet: ReviewResult | None = None,
) -> ReviewResult | None:
    packet = packet or git_review_packet(cwd)
    try:
        from core.execution_kernel import get_kernel

        kernel = get_kernel()
        if kernel.cancelled():
            return None
        goal = plan.goal if plan else ""
        criteria = ""
        if plan:
            crit = plan.success_criteria
            criteria = (
                f"pytest={crit.pytest_targets} py_compile={crit.py_compile} "
                f"touched={crit.py_compile_touched} notes={crit.notes}"
            )
        check_line = ""
        if check is not None:
            check_line = f"ok={check.ok} argv={check.argv} output={check.output[-1500:]}"
        user = (
            f"Goal: {goal}\nCriteria: {criteria}\n"
            f"Changed files: {packet.changed_paths}\n"
            f"Checks: {check_line}\n"
            f"Diff:\n{packet.patch[:8000]}"
        )
        result = kernel.run_model(
            [
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": user[:12000]},
            ],
            role="code",
            timeout_s=_REVIEW_TIMEOUT_S,
            format="json",
            temperature=0.1,
            max_output_tokens=500,
        )
        if not result.ok or result.cancelled:
            return None
        data = parse_llm_json(_model_text(result.value))
        return review_from_llm_payload(data, packet)
    except Exception as exc:
        logger.debug("llm reviewer fallback: %s", exc)
        return None


def review_coding_result(
    cwd: str | Path,
    plan: CodingPlan | None = None,
    check: CheckResult | None = None,
    *,
    use_llm: bool = True,
) -> ReviewResult:
    packet = git_review_packet(cwd)
    if use_llm:
        llm = try_llm_review(cwd, plan, check, packet)
        if llm is not None:
            return llm
    return packet
