"""Tool execution boundary and permission gate for the IMMORTALITY Custom Harness.

Enforces:
  1. Strict safety boundary below the model (validate schema before execution)
  2. Targeted read-before-edit policy (only on code-mutation tools where target text is required)
  3. Permission gating via core.permissions.decide (ALLOW, CONFIRM, DENY)
  4. Path normalization across project root and desktop
  5. Execution via ExecutionKernel / ToolRegistry with timing and tracing
  6. Structured ToolExecutionResult emission
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core.harness.contracts import ToolDecision, ToolExecutionResult, ToolRequest
from core.paths import normalize_path_key, sanitize_llm_path
from core.pending_action import format_confirmation_message, with_user_confirmation
from core.permissions import CONFIRM, DENY, decide, format_denial
from tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Tools that specifically modify existing file content and require inspecting the file first
TARGETED_EDIT_TOOLS = frozenset({
    "edit_file",
    "replace_lines",
    "insert_before",
    "insert_after",
    "replace_regex",
    "delete_block",
    "rename_symbol",
    "apply_patch",
})

# All mutating tools
ALL_MUTATING_TOOLS = TARGETED_EDIT_TOOLS | frozenset({"create_file", "write_file", "append_file", "delete_file"})


class HarnessToolExecutor:
    """Validates, permission-checks, and executes tool calls."""

    def __init__(
        self,
        project_root: str = "",
        desktop_path: str = "",
        registry: ToolRegistry | None = None,
    ) -> None:
        self.project_root = project_root
        self.desktop_path = desktop_path
        self.registry = registry or ToolRegistry()
        self.registry.setup()
        self.files_read: set[str] = set()
        self.read_counts: dict[str, int] = {}
        self.executed_tools: set[str] = set()
        self.before_snapshots: dict[str, str] = {}

    def normalize_path(self, path: str) -> str:
        """Sanitize and normalize paths relative to project root or desktop."""
        if not path:
            return path
        root = self.project_root or self.desktop_path
        sanitized = sanitize_llm_path(path, root)
        if sanitized:
            return sanitized
        if self.project_root:
            return normalize_path_key(path, self.project_root)
        return str(Path(path).resolve())

    def validate_request(self, tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Validate tool schema and argument constraints."""
        if not tool_name:
            return False, "Missing tool name."
        if tool_name not in self.registry._tools and tool_name.upper() != "DONE":
            return False, f"Unknown tool '{tool_name}'."
        if not isinstance(args, dict):
            return False, "Tool arguments must be a JSON dictionary."
        return True, ""

    def evaluate_policy(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        permission_mode: str = "assisted",
        require_edits: bool = False,
    ) -> ToolDecision:
        """Evaluate read-before-edit safety and permission gating."""
        normalized_args = dict(args)
        if "path" in normalized_args and isinstance(normalized_args["path"], str):
            normalized_args["path"] = self.normalize_path(normalized_args["path"])

        path = normalized_args.get("path", "")

        # 1. Targeted read-before-edit check
        if tool_name in TARGETED_EDIT_TOOLS and path:
            if path not in self.files_read:
                return ToolDecision(
                    action=DENY,
                    mode=permission_mode,
                    reason=f"Target file '{path}' must be inspected with read_file before applying targeted edits.",
                    tool_name=tool_name,
                    normalized_args=normalized_args,
                )

        # 2. Permission policy decision
        gate = decide(tool_name, normalized_args, mode=permission_mode)
        return ToolDecision(
            action=gate.action,
            mode=gate.mode,
            reason=gate.reason,
            tool_name=tool_name,
            normalized_args=normalized_args,
            is_destructive=gate.needs_confirm and "destructive" in gate.reason.lower(),
            needs_confirmation=gate.needs_confirm,
        )

    async def execute(
        self,
        decision: ToolDecision,
        *,
        step_index: int = 0,
        confirmed: bool = False,
        timeout_s: float = 60.0,
    ) -> ToolExecutionResult:
        """Execute an allowed or confirmed tool."""
        tool_name = decision.tool_name
        args = decision.normalized_args
        if confirmed:
            args = with_user_confirmation(tool_name, args)

        started = time.perf_counter()

        # Capture pre-edit snapshot if modifying a file
        path = args.get("path", "")
        if tool_name in ALL_MUTATING_TOOLS and path and path not in self.before_snapshots:
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    self.before_snapshots[path] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        # Execute through ExecutionKernel for tracing and timeouts
        try:
            from core.execution_kernel import get_kernel
            kres = await get_kernel().run_tool(tool_name, args, timeout_s=timeout_s)
            raw_res = kres.value if kres.ok else json.dumps(
                {"status": "error", "message": kres.error or "tool failed"}
            )
        except Exception:
            raw_res = await self.registry.execute(tool_name, args)

        duration = int((time.perf_counter() - started) * 1000)

        # Parse success or failure
        success = True
        error_msg = ""
        try:
            data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
            if isinstance(data, dict):
                if data.get("status") == "error":
                    success = False
                    error_msg = str(data.get("message") or data.get("result") or raw_res)
                inner = data.get("result")
                if isinstance(inner, dict) and inner.get("status") == "error":
                    success = False
                    error_msg = str(inner.get("message") or raw_res)
        except Exception:
            if '"status": "error"' in str(raw_res):
                success = False
                error_msg = str(raw_res)

        output_str = raw_res if isinstance(raw_res, str) else json.dumps(raw_res)

        # Track state if successful
        if success:
            self.executed_tools.add(tool_name)
            if tool_name == "read_file" and path:
                self.files_read.add(path)
                self.read_counts[path] = self.read_counts.get(path, 0) + 1
                # Also capture before snapshot on read
                if path not in self.before_snapshots:
                    try:
                        parsed = json.loads(output_str)
                        raw_c = parsed.get("result", {}).get("content")
                        if isinstance(raw_c, str):
                            self.before_snapshots[path] = raw_c
                    except Exception:
                        pass

        rollbackable = bool(tool_name in ALL_MUTATING_TOOLS and path)
        artifacts = [path] if path and tool_name in ALL_MUTATING_TOOLS else []

        return ToolExecutionResult(
            tool_name=tool_name,
            args=args,
            success=success,
            result=output_str,
            error=error_msg,
            duration_ms=duration,
            rollbackable=rollbackable,
            artifacts_touched=artifacts,
        )
