"""Phase 4 permission modes — kernel policy, not a second agent.

SAFE / ASSISTED / AUTONOMOUS / DEVELOPER. Destructive git is never silent.
Unsupported capabilities stay denied regardless of mode.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from core.pending_action import (
    CONFIRMATION_TOOLS,
    DIRECT_BROWSER_TOOLS,
    READ_ONLY_TOOLS,
    is_destructive_invocation,
    needs_confirmation,
)

MODE_SAFE = "safe"
MODE_ASSISTED = "assisted"
MODE_AUTONOMOUS = "autonomous"
MODE_DEVELOPER = "developer"
MODES = (MODE_SAFE, MODE_ASSISTED, MODE_AUTONOMOUS, MODE_DEVELOPER)

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

_ALWAYS_ALLOW = frozenset({"DONE"}) | READ_ONLY_TOOLS | DIRECT_BROWSER_TOOLS

_lock = threading.Lock()
_override: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    action: str
    mode: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW

    @property
    def denied(self) -> bool:
        return self.action == DENY

    @property
    def needs_confirm(self) -> bool:
        return self.action == CONFIRM


def normalize_mode(raw: str | None) -> str:
    mode = (raw or "").strip().lower()
    aliases = {
        "read": MODE_SAFE,
        "readonly": MODE_SAFE,
        "read-only": MODE_SAFE,
        "assist": MODE_ASSISTED,
        "confirm": MODE_ASSISTED,
        "auto": MODE_AUTONOMOUS,
        "dev": MODE_DEVELOPER,
    }
    mode = aliases.get(mode, mode)
    if mode not in MODES:
        return MODE_ASSISTED
    return mode


def parse_mode(raw: str | None) -> str | None:
    """Return a valid mode or None if the token is unknown."""
    mode = (raw or "").strip().lower()
    aliases = {
        "read": MODE_SAFE,
        "readonly": MODE_SAFE,
        "read-only": MODE_SAFE,
        "assist": MODE_ASSISTED,
        "confirm": MODE_ASSISTED,
        "auto": MODE_AUTONOMOUS,
        "dev": MODE_DEVELOPER,
    }
    mode = aliases.get(mode, mode)
    if mode in MODES:
        return mode
    return None


def get_mode() -> str:
    with _lock:
        if _override:
            return _override
    try:
        from core.agent_state import AgentState

        stored = getattr(AgentState(), "permission_mode", None)
        parsed = parse_mode(stored)
        if parsed:
            return parsed
    except Exception:
        pass
    env = (os.environ.get("IMMORTILITY_PERMISSION_MODE") or "").strip()
    parsed = parse_mode(env)
    if parsed:
        return parsed
    try:
        from core.config import get_config

        return normalize_mode(get_config().permission_mode)
    except Exception:
        return MODE_ASSISTED


def set_mode(raw: str) -> str:
    mode = parse_mode(raw)
    if mode is None:
        raise ValueError(
            f"Unknown permission mode '{raw}'. Use: {', '.join(MODES)}"
        )
    with _lock:
        global _override
        _override = mode
    try:
        from core.agent_state import AgentState

        state = AgentState()
        state.permission_mode = mode
        state.save()
    except Exception:
        pass
    try:
        from tools.hud_state import update_hud

        update_hud(permission_mode=mode)
    except Exception:
        pass
    return mode


def reset_mode_for_tests() -> None:
    global _override
    with _lock:
        _override = None


def mode_description(mode: str | None = None) -> str:
    mode = normalize_mode(mode or get_mode())
    return {
        MODE_SAFE: "read-only — mutating tools are denied",
        MODE_ASSISTED: "mutating tools ask for confirmation (default)",
        MODE_AUTONOMOUS: "known non-destructive writes auto-run; destructive still confirms",
        MODE_DEVELOPER: "unknown tools auto-run; destructive git/rm still confirms",
    }[mode]


def decide(
    tool_name: str,
    args: dict | None = None,
    *,
    mode: str | None = None,
) -> PermissionDecision:
    """ALLOW / CONFIRM / DENY for one tool call under the active (or given) mode."""
    args = args or {}
    active = normalize_mode(mode or get_mode())
    name = (tool_name or "").strip()

    if name in _ALWAYS_ALLOW:
        return PermissionDecision(ALLOW, active, "read-only or user-directed open")

    destructive = is_destructive_invocation(name, args)
    would_confirm = needs_confirmation(name, args)

    if active == MODE_SAFE:
        if would_confirm or destructive:
            return PermissionDecision(
                DENY,
                active,
                "SAFE mode blocks mutating tools — switch to /mode assisted",
            )
        return PermissionDecision(ALLOW, active, "read-only")

    if destructive:
        return PermissionDecision(
            CONFIRM,
            active,
            "destructive operations are never silent",
        )

    if active == MODE_ASSISTED:
        if would_confirm:
            return PermissionDecision(CONFIRM, active, "ASSISTED confirms mutations")
        return PermissionDecision(ALLOW, active, "no confirmation required")

    if active == MODE_AUTONOMOUS:
        if name in CONFIRMATION_TOOLS:
            return PermissionDecision(
                ALLOW, active, "AUTONOMOUS auto-approves known non-destructive tools"
            )
        if would_confirm:
            return PermissionDecision(
                CONFIRM, active, "unknown mutator still confirms in AUTONOMOUS"
            )
        return PermissionDecision(ALLOW, active, "no confirmation required")

    # DEVELOPER
    return PermissionDecision(
        ALLOW, active, "DEVELOPER auto-approves non-destructive tools"
    )


def format_denial(decision: PermissionDecision, tool_name: str) -> str:
    return (
        f"Blocked in {decision.mode.upper()} mode: `{tool_name}` — {decision.reason}."
    )
