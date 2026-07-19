"""Workflow executor — delegates coding steps to CodingWorkflow / SelfDebug."""

from __future__ import annotations

import logging
from typing import Any

from core.workflow_history import WorkflowHistory
from core.workflow_state import WorkflowState
from editing.coding_workflow import CodingWorkflow

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    def __init__(self, state: WorkflowState, history: WorkflowHistory) -> None:
        self.state = state
        self.history = history

    async def run_coding_workflow(
        self,
        workflow_id: str,
        goal: str,
        project_root: str,
        rag_context: str,
        planner_hint: str = "",
        error_text: str = "",
    ) -> dict[str, Any]:
        if error_text:
            from editing.self_debug import SelfDebugOrchestrator

            result = await SelfDebugOrchestrator.run_from_error(
                error_text, project_root, rag_context
            )
        else:
            workflow = CodingWorkflow(project_root=project_root, rag_context=rag_context)
            result = await workflow.run(goal, planner_hint=planner_hint)

        payload = {
            "success": result.success,
            "message": result.message,
            "files_modified": result.files_modified,
            "failed_patches": result.failed_patches,
        }
        self.history.log_event(workflow_id, "CODING_WORKFLOW_COMPLETE", payload=payload)
        return payload
