"""Task planning and decomposition for the IMMORTALITY Custom Harness.

Breaks complex user goals into ordered TaskSteps with expected outcomes,
while avoiding unnecessary multi-step overhead for straightforward queries.
"""

from __future__ import annotations

import logging
from typing import Any

from core.harness.contracts import BrainProvider
from core.json_utils import parse_llm_json
from core.task import Task, TaskStatus, TaskStep
from core.task_manager import TaskManager, get_task_manager

logger = logging.getLogger(__name__)


class HarnessPlanner:
    """Plans and decomposes multi-step goals into durable TaskSteps."""

    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self.task_manager = task_manager or get_task_manager()

    def infer_direct_plan(self, goal: str, require_edits: bool = False) -> list[tuple[str, str]]:
        """Fast-path deterministic plan for common request patterns."""
        lower = goal.lower()
        if "test" in lower and ("run" in lower or "pytest" in lower):
            return [
                ("Inspect repository test configuration", "Test configuration and command identified"),
                ("Run test suite", "Execution results observed"),
                ("Synthesize test report", "Final status reported"),
            ]
        if require_edits:
            return [
                ("Inspect target files and context", "Target files and surrounding code located"),
                ("Apply modifications safely", "Changes applied to target files"),
                ("Verify modifications against tests/syntax", "Mechanical and CI verification passed"),
            ]
        return [
            (goal.strip() or "Complete the request", "Result reported from tool output"),
        ]

    async def plan_task(
        self,
        task: Task,
        *,
        brain: BrainProvider | None = None,
        require_edits: bool = False,
    ) -> list[TaskStep]:
        """Generate and assign a plan to the task in TaskManager."""
        # Check if plan already exists (e.g. resumed task)
        if task.plan:
            return task.plan

        steps_data: list[tuple[str, str]] = []

        if brain is not None and require_edits and len(task.goal.split()) >= 8:
            prompt = (
                "You are the IMMORTALITY Planner. Break this task into 2 to 4 clear, sequential steps.\n"
                f"Task Goal: {task.goal}\n"
                f"Require Edits: {require_edits}\n\n"
                "Respond with a JSON array of objects with 'description' and 'expected_outcome'.\n"
                'Example: [{"description": "Inspect indexer.py", "expected_outcome": "Identify bug location"}]'
            )
            try:
                response = await brain.generate_response(
                    [{"role": "user", "content": prompt}],
                    role="brain",
                    timeout_s=30.0,
                )
                parsed = parse_llm_json(response)
                if isinstance(parsed, list) and parsed:
                    for item in parsed:
                        if isinstance(item, dict) and "description" in item:
                            desc = str(item.get("description", "")).strip()
                            outcome = str(item.get("expected_outcome", desc)).strip()
                            if desc:
                                steps_data.append((desc, outcome))
            except Exception as exc:
                logger.debug("LLM planning fallback to deterministic inference: %s", exc)

        if not steps_data:
            steps_data = self.infer_direct_plan(task.goal, require_edits=require_edits)

        self.task_manager.set_plan(task.id, steps_data)
        updated = self.task_manager.get_task(task.id)
        return updated.plan if updated else []
