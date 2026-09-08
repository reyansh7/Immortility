"""Phase 4 session resume / handoff — continue a prior run without a new agent.

Reads the existing AgentState snapshot. Does not restore stale tool
confirmations (those stay cleared on startup).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionHandoff:
    permission_mode: str
    active_project: str | None
    conversation_turns: int
    last_user: str
    last_assistant: str
    has_pending_action: bool
    pending_tool: str
    has_pending_coding: bool
    current_task_goal: str
    last_url: str
    saved_at: str


def _clip(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _last_role(history: list[dict[str, Any]], role: str) -> str:
    for item in reversed(history or []):
        if (item.get("role") or "") == role:
            return str(item.get("content") or "")
    return ""


def capture_handoff(state: Any | None = None) -> SessionHandoff:
    if state is None:
        from core.agent_state import AgentState

        state = AgentState()
    history = list(getattr(state, "conversation_history", None) or [])
    pending = getattr(state, "pending_action", None) or {}
    task = getattr(state, "current_task", None) or {}
    coding = getattr(state, "pending_coding_request", None)
    try:
        from core.permissions import get_mode

        mode = get_mode()
    except Exception:
        mode = str(getattr(state, "permission_mode", "") or "assisted")
    return SessionHandoff(
        permission_mode=mode,
        active_project=getattr(state, "active_project", None),
        conversation_turns=len(history),
        last_user=_clip(_last_role(history, "user")),
        last_assistant=_clip(_last_role(history, "assistant")),
        has_pending_action=bool(pending),
        pending_tool=str(pending.get("tool") or ""),
        has_pending_coding=bool(coding),
        current_task_goal=_clip(str(task.get("goal") or task.get("title") or "")),
        last_url=_clip(str(getattr(state, "last_url", "") or ""), 120),
        saved_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def format_handoff(handoff: SessionHandoff) -> str:
    project = handoff.active_project or "(none)"
    lines = [
        "Session resume",
        f"Permission mode: {handoff.permission_mode}",
        f"Active project: {project}",
        f"Conversation turns: {handoff.conversation_turns}",
    ]
    if handoff.last_user:
        lines.append(f"Last user: {handoff.last_user}")
    if handoff.last_assistant:
        lines.append(f"Last reply: {handoff.last_assistant}")
    if handoff.current_task_goal:
        lines.append(f"Current task: {handoff.current_task_goal}")
    if handoff.has_pending_coding:
        lines.append("Pending coding plan: yes — reply yes to continue it")
    if handoff.has_pending_action:
        tool = handoff.pending_tool or "unknown"
        lines.append(
            f"Stale tool confirmation ({tool}) was not restored — re-ask if still needed"
        )
    if handoff.last_url:
        lines.append(f"Last URL: {handoff.last_url}")
    lines.append(f"Snapshot: {handoff.saved_at}")
    return "\n".join(lines)


def resume_session() -> tuple[SessionHandoff, str]:
    """Reload persisted state and return a human handoff. Safe to call at startup."""
    from core.agent_state import AgentState

    state = AgentState()
    try:
        state.reload()
    except Exception:
        pass
    handoff = capture_handoff(state)
    return handoff, format_handoff(handoff)


def write_handoff_file(handoff: SessionHandoff | None = None) -> Path:
    from core.repo_paths import handoff_path

    snap = handoff or capture_handoff()
    path = handoff_path()
    path.write_text(format_handoff(snap) + "\n", encoding="utf-8")
    return path
