"""Workflow scheduler — pause/resume and step pacing."""

from __future__ import annotations

import asyncio
import logging

from core.workflow_state import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state

    async def wait_while_paused(self, workflow_id: str, poll_seconds: float = 0.5) -> None:
        while True:
            workflow = self.state.get_workflow(workflow_id)
            if not workflow or not workflow.get("paused"):
                return
            if workflow["status"] == WorkflowStatus.CANCELLED:
                return
            logger.debug("Workflow %s paused — waiting", workflow_id)
            await asyncio.sleep(poll_seconds)

    def pause(self, workflow_id: str) -> None:
        self.state.pause_workflow(workflow_id)

    def resume(self, workflow_id: str) -> None:
        self.state.resume_workflow(workflow_id)
