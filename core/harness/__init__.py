"""IMMORTALITY harness package.

Tracing (begin_turn, MODE_FAST, …) loads immediately.
The agent runtime (ImmortalityHarness, planner, tools) loads on first use.
"""

from __future__ import annotations

from core.harness.tracing import (
    KNOWN_MODES,
    MODE_AGENT,
    MODE_BACKGROUND,
    MODE_FAST,
    TraceEvent,
    begin_turn,
    current_scope,
    last_turn,
    last_turn_summary,
    new_id,
    record,
    reset_for_tests,
)

__all__ = [
    "ImmortalityHarness",
    "ContextBuilder",
    "HarnessPlanner",
    "HarnessToolExecutor",
    "HarnessVerifier",
    "RecoveryManager",
    "HarnessAgentLoop",
    "BrainProvider",
    "KernelBrainProvider",
    "MockBrainProvider",
    "ToolRequest",
    "ToolDecision",
    "ToolExecutionResult",
    "RecoveryDecision",
    "FailureClass",
    "ALL_MUTATING_TOOLS",
    "TARGETED_EDIT_TOOLS",
    "TraceEvent",
    "begin_turn",
    "current_scope",
    "record",
    "last_turn",
    "last_turn_summary",
    "reset_for_tests",
    "new_id",
    "MODE_FAST",
    "MODE_AGENT",
    "MODE_BACKGROUND",
    "KNOWN_MODES",
]

_LAZY = {
    "ImmortalityHarness": ("core.harness.engine", "ImmortalityHarness"),
    "ContextBuilder": ("core.harness.context_builder", "ContextBuilder"),
    "HarnessPlanner": ("core.harness.planner", "HarnessPlanner"),
    "HarnessToolExecutor": ("core.harness.tool_executor", "HarnessToolExecutor"),
    "HarnessVerifier": ("core.harness.verifier", "HarnessVerifier"),
    "RecoveryManager": ("core.harness.recovery", "RecoveryManager"),
    "HarnessAgentLoop": ("core.harness.agent_loop", "HarnessAgentLoop"),
    "BrainProvider": ("core.harness.brain_provider", "BrainProvider"),
    "KernelBrainProvider": ("core.harness.brain_provider", "KernelBrainProvider"),
    "MockBrainProvider": ("core.harness.brain_provider", "MockBrainProvider"),
    "ToolRequest": ("core.harness.contracts", "ToolRequest"),
    "ToolDecision": ("core.harness.contracts", "ToolDecision"),
    "ToolExecutionResult": ("core.harness.contracts", "ToolExecutionResult"),
    "RecoveryDecision": ("core.harness.contracts", "RecoveryDecision"),
    "FailureClass": ("core.harness.contracts", "FailureClass"),
    "ALL_MUTATING_TOOLS": ("core.harness.tool_executor", "ALL_MUTATING_TOOLS"),
    "TARGETED_EDIT_TOOLS": ("core.harness.tool_executor", "TARGETED_EDIT_TOOLS"),
}


def __getattr__(name: str):
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = spec
    import importlib

    value = getattr(importlib.import_module(mod_name), attr)
    globals()[name] = value
    return value
