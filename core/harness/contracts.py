"""Core contracts for the IMMORTALITY Custom Harness Runtime.

Defines the typed boundary between model output, tool request, permission decision,
tool execution, observation, recovery, and brain providers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from core.task import Observation, _now_iso


class FailureClass(str, Enum):
    TOOL_ERROR = "tool_error"
    SYNTAX_ERROR = "syntax_error"
    TEST_FAILURE = "test_failure"
    TARGET_NOT_FOUND = "target_not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    VERIFICATION_FAILED = "verification_failed"
    MALFORMED_OUTPUT = "malformed_output"
    NO_PROGRESS = "no_progress"
    STATE_CONFLICT = "state_conflict"
    WINDOW_NOT_FOUND = "window_not_found"
    CONTROL_NOT_FOUND = "control_not_found"
    CONTROL_DISABLED = "control_disabled"
    APPLICATION_NOT_FOUND = "application_not_found"
    EMERGENCY_STOP_TRIGGERED = "emergency_stop_triggered"


@dataclass(frozen=True)
class ToolRequest:
    """A proposed tool invocation from the reasoning model."""
    name: str
    args: dict[str, Any]
    step_index: int = 0
    raw_output: str = ""
    timestamp: str = field(default_factory=_now_iso)

    def sig(self) -> str:
        try:
            payload = json.dumps(self.args or {}, sort_keys=True, default=str)
        except Exception:
            payload = str(self.args)
        return f"{self.name}|{payload}"


@dataclass(frozen=True)
class ToolDecision:
    """Outcome of safety, policy, and permission gating for a proposed tool."""
    action: str                        # "allow" | "confirm" | "deny"
    mode: str                          # "safe" | "assisted" | "autonomous" | "developer"
    reason: str
    tool_name: str
    normalized_args: dict[str, Any]
    is_destructive: bool = False
    needs_confirmation: bool = False

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def denied(self) -> bool:
        return self.action == "deny"

    @property
    def requires_confirmation(self) -> bool:
        return self.action == "confirm"


@dataclass(frozen=True)
class ToolExecutionResult:
    """The outcome of executing an allowed tool."""
    tool_name: str
    args: dict[str, Any]
    success: bool
    result: str
    error: str = ""
    duration_ms: int = 0
    rollbackable: bool = False
    artifacts_touched: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_now_iso)

    def to_observation(self, step_index: int) -> Observation:
        """Convert into a durable task Observation."""
        return Observation.create(
            step_index=step_index,
            tool_name=self.tool_name,
            args=self.args,
            result=self.result if self.success else (self.error or self.result),
            success=self.success,
        )


@dataclass(frozen=True)
class RecoveryDecision:
    """Instruction from RecoveryManager on how to handle a step failure."""
    action: str                        # "retry" | "replan" | "rollback" | "escalate" | "halt"
    failure_type: str                  # from FailureClass or string
    reason: str
    retry_count: int
    hint: str = ""
    rollback_checkpoint: str | None = None
    stop_execution: bool = False


class BrainProvider(Protocol):
    """Abstract interface for model reasoning to prevent provider lock-in."""

    async def generate_response(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "brain",
        timeout_s: float | None = None,
        **kwargs: Any,
    ) -> str: ...
