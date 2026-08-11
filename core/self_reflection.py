"""Post-task reflection → ExperienceMemory + KG + TurboVec."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Lesson:
    attempted: str
    tools: list[str]
    result: str
    error: str
    solution: str
    lesson: str
    confidence: float
    success: bool = False


def reflect_and_store(
    *,
    task: str,
    tools_used: list[str] | None = None,
    result: str = "",
    error: str = "",
    solution: str = "",
    success: bool = False,
    project: str | None = None,
    confidence: float = 0.7,
) -> Lesson:
    """Store a structured lesson after ACTION/PROJECT/RESEARCH work."""
    lesson = Lesson(
        attempted=(task or "")[:500],
        tools=list(tools_used or [])[:20],
        result=(result or "")[:1000],
        error=(error or "")[:1000],
        solution=(solution or result or "")[:1000],
        lesson=_derive_lesson(task, error, solution, success),
        confidence=float(confidence),
        success=bool(success),
    )
    try:
        from memory.experience_memory import ExperienceMemory

        ExperienceMemory().record(
            task=lesson.attempted,
            outcome="success" if lesson.success else "failure",
            details=(
                f"tools={lesson.tools}; error={lesson.error}; "
                f"solution={lesson.solution}; lesson={lesson.lesson}"
            )[:1000],
        )
    except Exception as exc:
        logger.debug("experience record skipped: %s", exc)

    try:
        from knowledge.engine import KnowledgeEngine

        KnowledgeEngine().add_reflection(
            task=lesson.attempted,
            what_broke="" if lesson.success else (lesson.error or lesson.result)[:2000],
            what_fixed_it=(lesson.solution if lesson.success else lesson.lesson)[:2000],
            files_modified=[],
            project=project,
        )
    except Exception as exc:
        logger.debug("kg reflection skipped: %s", exc)

    try:
        from knowledge.learner import remember_text

        remember_text(
            f"Lesson ({'ok' if lesson.success else 'fail'}): {lesson.attempted}\n"
            f"{lesson.lesson}\nTools: {', '.join(lesson.tools)}",
            source="self_reflection",
            kind="lesson",
        )
    except Exception:
        pass

    try:
        from memory.experience_dataset import append_experience_record

        if lesson.success or lesson.confidence >= 0.5:
            append_experience_record(asdict(lesson))
    except Exception:
        pass

    return lesson


def _derive_lesson(task: str, error: str, solution: str, success: bool) -> str:
    if success:
        return f"Succeeded on '{task[:120]}' via: {(solution or 'completed')[:200]}"
    if error:
        return f"Failed on '{task[:120]}': {error[:200]}. Avoid repeating this approach."
    return f"Incomplete on '{task[:120]}'."


def reflect_from_action_result(
    user_input: str,
    *,
    tools_executed: set[str] | list[str] | None = None,
    message: str = "",
    success: bool = True,
    project: str | None = None,
) -> Lesson | None:
    if not (user_input or "").strip():
        return None
    return reflect_and_store(
        task=user_input,
        tools_used=list(tools_executed or []),
        result=message,
        error="" if success else message,
        solution=message if success else "",
        success=success,
        project=project,
        confidence=0.75 if success else 0.6,
    )
