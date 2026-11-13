"""Selective, token-budgeted context builder for the IMMORTALITY Custom Harness.

Assembles precise system and turn prompts from:
  - Task goal, plan, and progress
  - Recent observations (budgeted)
  - User model & preferences
  - World model & active project context
  - Relevant semantic & experience memories
  - Active recovery hints
  - Tool catalog with permissions tags

Prevents dumping the entire database into every turn prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from core.task import Task

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Builds scoped, token-budgeted prompts for the reasoning brain."""

    def __init__(
        self,
        max_context_chars: int = 6000,
        max_observation_chars: int = 4000,
        max_memory_chars: int = 2000,
    ) -> None:
        self.max_context_chars = max_context_chars
        self.max_observation_chars = max_observation_chars
        self.max_memory_chars = max_memory_chars

    def build_system_prompt(
        self,
        task: Task,
        *,
        tools_catalog: str,
        user_context: str = "",
        world_context: str = "",
        memory_context: str = "",
        recovery_hints: list[str] | None = None,
        require_edits: bool = False,
        context_override: str = "",
    ) -> str:
        """Construct the high-level system prompt for the agent turn."""
        from core.config import load_prompt_file
        from core.repo_paths import get_repo_root

        hints = recovery_hints or []
        sections: list[str] = []

        # 1. Base identity & behavioral invariants
        sections.append(
            "You are IMMORTALITY, a persistent personal intelligence runtime / JARVIS.\n"
            "You reason carefully over evidence, execute actions through tools, verify outcomes, "
            "and produce reliable results."
        )

        # 2. Operating mode & completion criteria
        if require_edits:
            sections.append(
                "MODE: EDIT / MUTATE\n"
                "- Apply real filesystem or code changes using editing tools.\n"
                "- Before modifying existing code files, inspect or read them first to ensure exact matches.\n"
                "- NEVER call DONE until changes are made and verified."
            )
        else:
            sections.append(
                "MODE: INVESTIGATION / ANALYSIS\n"
                "- Investigate, inspect files/directories, and gather evidence.\n"
                "- Do NOT modify files unless explicitly requested by the user.\n"
                "- When you have sufficient evidence, call DONE with a clear, complete plain-text report."
            )

        # 3. User & World State
        if user_context:
            sections.append(f"### USER PROFILE & PREFERENCES\n{user_context[:1000]}")
        if world_context:
            sections.append(f"### WORLD & PROJECT STATE\n{world_context[:1500]}")

        # 4. Relevant Memory & Context Override
        mem_block = ""
        if memory_context:
            mem_block += f"### RELEVANT EXPERIENCE & MEMORY\n{memory_context[:self.max_memory_chars]}\n\n"
        if context_override:
            mem_block += f"### RETRIEVED CONTEXT\n{context_override[:self.max_context_chars]}\n\n"
        if mem_block.strip():
            sections.append(mem_block.strip())

        # 5. Recovery Hints / Active Diagnoses
        if hints:
            hints_str = "\n".join(f"- {h}" for h in hints[-4:])
            sections.append(
                f"### ACTIVE RECOVERY INSTRUCTIONS (PREVIOUS STEP FAILED)\n{hints_str}"
            )

        # 6. Task Plan & Progress
        if task.plan:
            plan_lines = []
            for step in task.plan:
                marker = "[x]" if step.status == "completed" else ("[->]" if step.index == task.current_step else "[ ]")
                plan_lines.append(f"{marker} Step {step.index + 1}: {step.description} (Expected: {step.expected_outcome})")
            sections.append("### TASK PLAN\n" + "\n".join(plan_lines))

        # 7. Available Tools & Format Contract
        sections.append(
            "### TOOL CATALOG\n"
            f"{tools_catalog}\n\n"
            "### INVOCATION PROTOCOL\n"
            "Respond with ONE valid JSON object per turn:\n"
            '{"tool": "<tool_name>", "args": {<arguments>}}\n\n'
            "When the task is completely finished, call DONE:\n"
            '{"tool": "DONE", "args": {"message": "<final summary or findings>"}}'
        )

        return "\n\n".join(sections)

    def build_step_prompt(
        self,
        task: Task,
        current_step_index: int,
        recent_observations: list[Any] | None = None,
    ) -> str:
        """Construct the turn prompt reflecting the current task state."""
        obs = recent_observations or task.observations[-5:]
        lines = [f"Goal: {task.goal}"]

        if task.plan and 0 <= current_step_index < len(task.plan):
            step = task.plan[current_step_index]
            lines.append(f"Current Step: {step.description}")

        if obs:
            lines.append("Recent Observations:")
            for o in obs:
                res_preview = getattr(o, "result_summary", str(o))[:600]
                tool_name = getattr(o, "tool_name", "tool")
                lines.append(f"- [{tool_name}] {res_preview}")

        lines.append("Propose your next tool call as JSON, or call DONE if complete.")
        return "\n".join(lines)
