"""Recovery manager and failure classification for the IMMORTALITY Custom Harness.

Implements bounded, diagnosis-driven recovery:
  - Classifies failures into standard FailureClass
  - Enforces retry budgets (max 3 per class, max 2 identical calls)
  - Detects no-progress loops
  - Emits targeted, actionable recovery hints
  - Coordinates bounded file rollbacks via TaskManager / CheckpointManager
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.harness.contracts import FailureClass, RecoveryDecision
from core.task import Task
from core.task_manager import TaskManager, get_task_manager

logger = logging.getLogger(__name__)


class RecoveryManager:
    """Classifies errors, tracks retry budgets, and produces recovery strategies."""

    def __init__(
        self,
        max_retries_per_class: int = 3,
        max_identical_failures: int = 2,
        task_manager: TaskManager | None = None,
    ) -> None:
        self.max_retries_per_class = max_retries_per_class
        self.max_identical_failures = max_identical_failures
        self.task_manager = task_manager or get_task_manager()
        self.failure_counts: dict[str, int] = {}
        self.identical_signatures: dict[str, int] = {}
        self.consecutive_no_progress: int = 0

    def classify_failure(self, error_message: str, tool_name: str = "") -> str:
        """Categorize an error string into a standardized FailureClass."""
        err_lower = (error_message or "").lower()
        if "target text not found" in err_lower or "could not find target" in err_lower:
            return FailureClass.TARGET_NOT_FOUND.value
        if "syntax" in err_lower or "syntaxerror" in err_lower or "indentationerror" in err_lower:
            return FailureClass.SYNTAX_ERROR.value
        if "test failed" in err_lower or "pytest" in err_lower or "assertionerror" in err_lower:
            return FailureClass.TEST_FAILURE.value
        if "denied" in err_lower or "blocked in" in err_lower:
            return FailureClass.PERMISSION_DENIED.value
        if "timed out" in err_lower or "timeout" in err_lower:
            return FailureClass.TIMEOUT.value
        if "verification failed" in err_lower or "ci(" in err_lower or "mechanical(" in err_lower:
            return FailureClass.VERIFICATION_FAILED.value
        if "json" in err_lower or "invalid json" in err_lower or "malformed" in err_lower:
            return FailureClass.MALFORMED_OUTPUT.value
        if "window not found" in err_lower:
            return FailureClass.WINDOW_NOT_FOUND.value
        if "element not found" in err_lower or "control not found" in err_lower:
            return FailureClass.CONTROL_NOT_FOUND.value
        if "control disabled" in err_lower:
            return FailureClass.CONTROL_DISABLED.value
        if "application not found" in err_lower or "application launch failed" in err_lower:
            return FailureClass.APPLICATION_NOT_FOUND.value
        if "emergency stop" in err_lower:
            return FailureClass.EMERGENCY_STOP_TRIGGERED.value
        if "no progress" in err_lower:
            return FailureClass.NO_PROGRESS.value
        return FailureClass.TOOL_ERROR.value

    def generate_hint(self, failure_class: str, details: str, tool_name: str = "") -> str:
        """Produce an actionable correction hint for the next model turn."""
        if failure_class == FailureClass.TARGET_NOT_FOUND.value:
            return (
                "HINT: Target text was not found. Copy target_text EXACTLY from read_file 'content' field "
                "(do not include line numbers or altered whitespace)."
            )
        if failure_class == FailureClass.SYNTAX_ERROR.value:
            return (
                "HINT: A syntax error was introduced. Use write_file with the COMPLETE corrected content, "
                "or re-read the file before modifying."
            )
        if failure_class == FailureClass.PERMISSION_DENIED.value:
            return (
                f"HINT: Tool '{tool_name}' was blocked by permission policy. "
                "Do NOT retry the same forbidden action. Choose a permitted tool or request user guidance."
            )
        if failure_class == FailureClass.VERIFICATION_FAILED.value:
            return f"HINT: Verification failed: {details[:400]}. Review the errors and repair the code."
        if failure_class == FailureClass.MALFORMED_OUTPUT.value:
            return "HINT: Malformed output. Respond ONLY with a valid JSON object matching the invocation protocol."
        if failure_class == FailureClass.WINDOW_NOT_FOUND.value:
            return "HINT: Window not found. Call computer_list_windows to find available windows, or computer_launch_app to start it."
        if failure_class == FailureClass.CONTROL_NOT_FOUND.value:
            return "HINT: UI control not found. Call computer_get_accessibility_tree to inspect active controls, or verify element name/role."
        if failure_class == FailureClass.APPLICATION_NOT_FOUND.value:
            return "HINT: Application executable not found. Verify app name or supply a specific project path."
        if failure_class == FailureClass.EMERGENCY_STOP_TRIGGERED.value:
            return "HINT: Emergency Stop is active. Computer actions are blocked until manually cleared."
        return f"HINT: Tool error: {details[:200]}. Re-examine arguments and try an alternative step."

    def evaluate(
        self,
        task: Task,
        error_message: str,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
    ) -> RecoveryDecision:
        """Evaluate a failure and determine whether to retry, replan, rollback, or halt."""
        args_payload = json.dumps(tool_args or {}, sort_keys=True, default=str)
        call_sig = f"{tool_name}|{args_payload}|{error_message[:150]}"

        # 1. Identical consecutive failure check
        self.identical_signatures[call_sig] = self.identical_signatures.get(call_sig, 0) + 1
        if self.identical_signatures[call_sig] >= self.max_identical_failures:
            return RecoveryDecision(
                action="halt",
                failure_type=FailureClass.NO_PROGRESS.value,
                reason=f"Tool call failed identically {self.identical_signatures[call_sig]} times; halting to avoid unsafe loops.",
                retry_count=self.identical_signatures[call_sig],
                hint="Repeated identical failure. Stop loop.",
                stop_execution=True,
            )

        # 2. Classify error
        f_class = self.classify_failure(error_message, tool_name)
        self.failure_counts[f_class] = self.failure_counts.get(f_class, 0) + 1
        retries = self.failure_counts[f_class]

        hint = self.generate_hint(f_class, error_message, tool_name)

        # 3. Check retry budget for this failure class
        if retries > self.max_retries_per_class:
            # Check if rollback is feasible
            rollback_ckpt = None
            if task.checkpoints:
                rollback_ckpt = task.checkpoints[-1]
                logger.warning("Rolling back task %s after exceeding retries for %s", task.id[:8], f_class)
                self.task_manager.rollback_task(task.id)

            return RecoveryDecision(
                action="rollback" if rollback_ckpt else "halt",
                failure_type=f_class,
                reason=f"Exceeded maximum retries ({self.max_retries_per_class}) for failure class '{f_class}'.",
                retry_count=retries,
                hint=hint,
                rollback_checkpoint=rollback_ckpt,
                stop_execution=True,
            )

        self.consecutive_no_progress += 1
        if self.consecutive_no_progress >= 4:
            return RecoveryDecision(
                action="halt",
                failure_type=FailureClass.NO_PROGRESS.value,
                reason="Four consecutive steps failed with no progress; halting execution.",
                retry_count=retries,
                hint=hint,
                stop_execution=True,
            )

        return RecoveryDecision(
            action="retry",
            failure_type=f_class,
            reason=f"Retryable failure ({retries}/{self.max_retries_per_class}).",
            retry_count=retries,
            hint=hint,
            stop_execution=False,
        )

    def record_progress(self) -> None:
        """Reset consecutive no-progress counter upon a successful step."""
        self.consecutive_no_progress = 0
