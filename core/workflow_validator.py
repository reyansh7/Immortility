"""Validates workflow context before execution."""

from __future__ import annotations

from typing import Any


class WorkflowValidator:
    @staticmethod
    def validate_context(context: dict[str, Any]) -> tuple[bool, str]:
        if not context.get("goal"):
            return False, "Workflow goal is required"
        return True, "OK"
