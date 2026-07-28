"""Run several deterministic HUD actions from one user message.

The 8B action loop is unreliable for multi-step prompts like
\"create a folder named X and then open youtube\". This module
executes matching local skills in order and returns a combined reply.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def run_multi_actions(message: str) -> str | None:
    """Execute all matching deterministic actions. Returns combined reply or None."""
    msg = (message or "").strip()
    if not msg:
        return None

    parts: list[str] = []

    # 1) Desktop folder create
    try:
        from tools.desktop_fs import create_desktop_folder, wants_create_folder

        if wants_create_folder(msg):
            parts.append(create_desktop_folder(msg))
    except Exception as exc:
        logger.exception("multi: folder create failed")
        parts.append(f"Folder create failed: {exc}")

    # 2) Browser / search (Chrome profile)
    try:
        from tools.user_browser import handle_browser_request, wants_browser_action

        if wants_browser_action(msg):
            reply = handle_browser_request(msg)
            if reply:
                parts.append(reply)
    except Exception as exc:
        logger.exception("multi: browser failed")
        parts.append(f"Browser open failed: {exc}")

    # 3) Local apps (chrome/vscode/…) — skip if browser already opened
    try:
        low = msg.lower()
        if any(w in low for w in ("open ", "launch ", "start ")) and re.search(
            r"\b(chrome|brave|vscode|vs code|notepad|explorer|terminal|powershell|whatsapp)\b",
            low,
        ):
            # Avoid double-open when Chrome tabs were already launched
            if not any("Chrome profile" in p or "Opened in your" in p for p in parts):
                from tools.app_tool import AppTool

                if "chrome" in low:
                    parts.append(AppTool.open_application("chrome").get("message") or "Opened chrome")
    except Exception as exc:
        logger.debug("multi: apps: %s", exc)

    if not parts:
        return None

    # If we handled at least one deterministic action, and the leftover intent
    # is only connectors ("and then", "also") — don't fall through to LLM.
    return "\n".join(parts)


def multi_actions_complete(message: str, reply: str | None) -> bool:
    """True when every detectable fast-path intent in the message was handled."""
    if not reply:
        return False
    low = (message or "").lower()

    try:
        from tools.desktop_fs import wants_create_folder
        from tools.user_browser import wants_browser_action

        needed_folder = wants_create_folder(low)
        needed_browser = wants_browser_action(low)
    except Exception:
        return False

    got_folder = "Created folder" in reply or "already exists" in reply or "Folder create failed" in reply
    got_browser = "Opened in your" in reply or "Chrome profile" in reply or "Browser open failed" in reply

    if needed_folder and not got_folder:
        return False
    if needed_browser and not got_browser:
        return False

    # Still needs coding / leetcode / analysis → not complete
    if re.search(
        r"\b(solve|implement|write\s+code|refactor|fix\s+bug|leetcode\s+\d+)\b",
        low,
    ):
        return False
    return needed_folder or needed_browser
