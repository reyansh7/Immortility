"""Decision engine — workflow step transitions and retry policy."""

from __future__ import annotations

from core.workflow_state import WorkflowStep


def _max_retries() -> int:
    try:
        from core.config import get_config

        return get_config().decision_max_retries
    except Exception:
        return 3


# Kept for import compatibility; prefer _max_retries() at call sites
MAX_RETRIES = 3

# Linear pipeline with debugger loop on verification failure
_PIPELINE: list[WorkflowStep] = [
    WorkflowStep.PLANNING,
    WorkflowStep.RESEARCH,
    WorkflowStep.KNOWLEDGE_RETRIEVAL,
    WorkflowStep.EDIT_PLANNER,
    WorkflowStep.PATCH_GENERATOR,
    WorkflowStep.EXECUTOR,
    WorkflowStep.VERIFIER,
    WorkflowStep.REFLECTOR,
    WorkflowStep.EXPERIENCE_UPDATE,
]


class DecisionEngine:
    @staticmethod
    def get_next_step(
        current: WorkflowStep,
        success: bool,
        retry_count: int = 0,
    ) -> WorkflowStep | None:
        if not success:
            if retry_count + 1 >= _max_retries():
                return None
            if current in (
                WorkflowStep.VERIFIER,
                WorkflowStep.PATCH_GENERATOR,
                WorkflowStep.EXECUTOR,
            ):
                return WorkflowStep.DEBUGGER
            if current == WorkflowStep.DEBUGGER:
                return WorkflowStep.PATCH_GENERATOR
            return current  # retry same step

        try:
            idx = _PIPELINE.index(current)
        except ValueError:
            return None
        if idx + 1 >= len(_PIPELINE):
            return None
        return _PIPELINE[idx + 1]
