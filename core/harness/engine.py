"""Master runtime engine for the IMMORTALITY Custom Harness.

Coordinates:
  - Task lifecycle and durable SQLite persistence
  - Planning and step management
  - Context construction with UserModel and WorldModel
  - Safety and permission boundary
  - Tool execution and observation recording
  - Hierarchical verification
  - Bounded failure recovery
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from core.harness.agent_loop import HarnessAgentLoop
from core.harness.brain_provider import BrainProvider, KernelBrainProvider
from core.harness.context_builder import ContextBuilder
from core.harness.contracts import ToolDecision
from core.harness.planner import HarnessPlanner
from core.harness.recovery import RecoveryManager
from core.harness.tool_executor import HarnessToolExecutor
from core.harness.verifier import HarnessVerifier
from core.paths import get_desktop_path
from core.permissions import get_mode
from core.task import Task, TaskStatus, TaskStep
from core.task_manager import TaskManager, get_task_manager

logger = logging.getLogger(__name__)

_HEAVY_ACTION = (
    "implement",
    "refactor",
    "debug",
    "commit",
    "push",
    "fix the",
    "make the changes",
    "and then",
)


def is_simple_action(goal: str, require_edits: bool) -> bool:
    """True for a one-shot open/read/list/command — skip SQLite task + planner LLM."""
    if require_edits:
        return False
    text = (goal or "").strip()
    if not text or len(text) > 240 or len(text.split()) > 22:
        return False
    low = text.lower()
    return not any(h in low for h in _HEAVY_ACTION)


class ImmortalityHarness:
    """IMMORTALITY's first-party autonomous intelligence runtime."""

    def __init__(
        self,
        task_manager: TaskManager | None = None,
        brain: BrainProvider | None = None,
        project_root: str = "",
    ) -> None:
        self.task_manager = task_manager or get_task_manager()
        self.brain = brain or KernelBrainProvider()

        # Resolve active project root if not supplied
        if not project_root:
            try:
                from knowledge.engine import KnowledgeEngine
                active = KnowledgeEngine().get_active_project()
                if active and active.path:
                    project_root = active.path
            except Exception:
                pass

        self.project_root = project_root
        self.desktop_path = str(get_desktop_path())

        self.context_builder = ContextBuilder()
        self.planner = HarnessPlanner(task_manager=self.task_manager)
        self.tool_executor = HarnessToolExecutor(
            project_root=self.project_root,
            desktop_path=self.desktop_path,
        )
        self.verifier = HarnessVerifier(project_root=self.project_root)
        self.recovery_manager = RecoveryManager(task_manager=self.task_manager)
        self.agent_loop = HarnessAgentLoop(
            task_manager=self.task_manager,
            context_builder=self.context_builder,
            tool_executor=self.tool_executor,
            verifier=self.verifier,
            recovery_manager=self.recovery_manager,
        )

    def _collect_user_and_world_context(self) -> tuple[str, str, str]:
        """Query user model, world model, and relevant experiences."""
        user_ctx = ""
        world_ctx = ""
        mem_ctx = ""

        try:
            from core.user_model import UserModel
            user_ctx = UserModel.get_instance().get_context_summary()
        except Exception:
            try:
                from memory.user_profile import get_user_name
                user_ctx = f"User Name: {get_user_name()}"
            except Exception:
                pass

        try:
            from core.world_model import WorldModel
            world_ctx = WorldModel.get_instance(self.project_root).get_summary()
        except Exception:
            if self.project_root:
                world_ctx = f"Project Root: {self.project_root}"

        try:
            from memory.experience_memory import ExperienceMemory, format_experiences_block
            experiences = ExperienceMemory().get_recent_experiences(limit=3)
            mem_ctx = format_experiences_block(experiences)
        except Exception:
            pass

        return user_ctx, world_ctx, mem_ctx

    async def run(
        self,
        goal: str,
        *,
        require_edits: bool = False,
        context_override: str = "",
        auto_confirm: bool = False,
        permission_mode: str | None = None,
        max_steps: int = 15,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        internal_history: list[dict[str, str]] | None = None,
        task_id: str | None = None,
        skip_confirm_sigs: set[str] | None = None,
    ) -> str:
        """Execute a user goal through the harness runtime."""
        active_mode = permission_mode or get_mode()
        simple = is_simple_action(goal, require_edits) and not task_id

        if task_id:
            task = self.task_manager.get_task(task_id)
            if not task:
                task = self.task_manager.create_task(
                    goal=goal, agent="immortality", permission_mode=active_mode
                )
        elif simple:
            task = Task.create(goal=goal, agent="immortality", permission_mode=active_mode)
            task.plan = [
                TaskStep(
                    index=0,
                    description=goal,
                    expected_outcome="Request completed from tool output",
                )
            ]
        else:
            task = self.task_manager.create_task(
                goal=goal, agent="immortality", permission_mode=active_mode
            )

        if not simple:
            await self.planner.plan_task(
                task,
                brain=self.brain,
                require_edits=require_edits,
            )
            user_ctx, world_ctx, mem_ctx = self._collect_user_and_world_context()
        else:
            user_ctx = world_ctx = mem_ctx = ""

        result = await self.agent_loop.run_loop(
            task,
            self.brain,
            max_steps=max_steps if not simple else min(max_steps, 8),
            require_edits=require_edits,
            auto_confirm=auto_confirm,
            context_override=context_override,
            user_context=user_ctx,
            world_context=world_ctx,
            memory_context=mem_ctx,
            internal_history=internal_history,
            on_event=on_event,
            skip_confirm_sigs=skip_confirm_sigs,
            persist=not simple,
        )

        return result

    async def resume_task(
        self,
        task_id: str,
        *,
        auto_confirm: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Resume an interrupted or paused task from its persisted state."""
        task = self.task_manager.get_task(task_id)
        if not task:
            return f"Task '{task_id}' not found."

        # Mark resumed
        task = self.task_manager.resume_task(task_id) or task

        # Reconstruct internal conversation history from observations
        history: list[dict[str, str]] = []
        for obs in task.observations:
            history.append({
                "role": "assistant",
                "content": f'{{"tool": "{obs.tool_name}", "args": {obs.args_summary}}}',
            })
            history.append({
                "role": "user",
                "content": f"Tool result: {obs.result_summary}\n\nNext step? (JSON)",
            })

        user_ctx, world_ctx, mem_ctx = self._collect_user_and_world_context()

        return await self.agent_loop.run_loop(
            task,
            self.brain,
            max_steps=12,
            require_edits=False,
            auto_confirm=auto_confirm,
            user_context=user_ctx,
            world_context=world_ctx,
            memory_context=mem_ctx,
            internal_history=history,
            on_event=on_event,
        )

    async def resume_confirmed(
        self,
        pending: dict[str, Any] | str,
        *,
        auto_confirm: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Execute a user-approved pending action and continue the harness loop."""
        if isinstance(pending, str):
            task_obj = self.task_manager.get_task(pending)
            if task_obj and task_obj.pending_action:
                pending = task_obj.pending_action
            else:
                raise ValueError(f"No pending action found for task ID: {pending}")

        tool_name = str(pending.get("tool") or "")
        args = pending.get("args") or {}
        task_id = pending.get("task_id") or ""
        require_edits = bool(pending.get("require_edits", False))
        context_override = pending.get("context_override") or ""

        # Execute confirmed tool
        decision = ToolDecision(
            action="allow",
            mode="assisted",
            reason="User confirmed action",
            tool_name=tool_name,
            normalized_args=args,
        )
        exec_res = await self.tool_executor.execute(decision, confirmed=True)

        # Restore or retrieve Task
        task = self.task_manager.get_task(task_id) if task_id else None
        if not task:
            task = self.task_manager.create_task(
                goal=f"Execute confirmed {tool_name}",
                agent="immortality",
            )

        # Record observation
        self.task_manager.record_observation(
            task.id,
            step_index=task.current_step,
            tool_name=tool_name,
            args=args,
            result=exec_res.result,
            success=exec_res.success,
        )

        # Clear pending action in store
        self.task_manager.store.update_pending_action(task.id, None)

        # Reconstruct history
        history = list(pending.get("internal_history") or [])
        preview = exec_res.result[:2000]
        history.append({
            "role": "user",
            "content": (
                f"Tool result: {preview}\n\n"
                "This tool already ran successfully with user approval. "
                "Next step? (JSON) If complete, call DONE with a clear report."
            ),
        })

        call_sig = f"{tool_name}|{json.dumps(args, sort_keys=True)}"
        user_ctx, world_ctx, mem_ctx = self._collect_user_and_world_context()

        return await self.agent_loop.run_loop(
            task,
            self.brain,
            max_steps=12,
            require_edits=require_edits,
            auto_confirm=auto_confirm,
            context_override=context_override,
            user_context=user_ctx,
            world_context=world_ctx,
            memory_context=mem_ctx,
            internal_history=history,
            on_event=on_event,
            skip_confirm_sigs={call_sig},
        )
