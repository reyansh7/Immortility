"""Phase 4 control-plane slash commands — /mode /resume /handoff.

Shared by the CLI and HUD so permission/session policy is not UI-specific.
"""

from __future__ import annotations

CONTROL_COMMANDS = frozenset({"/mode", "/resume", "/handoff"})


def is_control_command(text: str) -> bool:
    first = (text or "").strip().split(None, 1)[0].lower() if (text or "").strip() else ""
    return first in CONTROL_COMMANDS


def handle_control_command(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    parts = raw.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/mode":
        from core.permissions import (
            MODES,
            get_mode,
            mode_description,
            parse_mode,
            set_mode,
        )

        if not arg:
            mode = get_mode()
            return (
                f"Permission mode: {mode}\n"
                f"{mode_description(mode)}\n"
                f"Switch with /mode {' | '.join(MODES)}"
            )
        if parse_mode(arg) is None:
            return f"Unknown mode '{arg}'. Use: {', '.join(MODES)}"
        mode = set_mode(arg)
        return f"Permission mode set to {mode} — {mode_description(mode)}"

    if cmd == "/resume":
        from memory.session_resume import resume_session

        _handoff, text_out = resume_session()
        return text_out

    if cmd == "/handoff":
        from memory.session_resume import write_handoff_file

        path = write_handoff_file()
        return f"Handoff written to {path}"

    return None
