"""Deterministic Desktop filesystem actions — no LLM required."""

from __future__ import annotations

import re
from pathlib import Path

from core.paths import get_desktop_path

_CREATE_FOLDER = re.compile(
    r"\b(create|make|new)\b.{0,40}\bfolder\b",
    re.IGNORECASE,
)


def wants_create_folder(message: str) -> bool:
    low = (message or "").lower()
    if not low.strip():
        return False
    if not _CREATE_FOLDER.search(low):
        return False
    # Explicit desktop / home, or bare "create a new folder named X"
    if "desktop" in low or "named" in low or "called" in low or "name" in low:
        return True
    # "create a new folder" alone → Desktop by default
    if re.search(r"\b(create|make)\b.{0,20}\b(a\s+)?(new\s+)?folder\b", low):
        return True
    return False


def _extract_folder_name(message: str) -> str:
    low = (message or "").strip()
    ban = {
        "on", "in", "my", "the", "desktop", "a", "new", "please", "too",
        "also", "then", "and", "now", "here", "there", "youtube", "netflix",
        "google", "chrome", "browser", "folder", "named", "called",
    }
    patterns = [
        # Prefer explicit names: named/called X
        r"\b(?:named|called)\s+[\"']?([A-Za-z0-9][\w ._-]{0,60})",
        r"\bfolder\b.{0,20}\b(?:names?|name)\s+[\"']?([A-Za-z0-9][\w ._-]{0,60})",
        # create folder X on desktop
        r"\bfolder\b\s+[\"']?([A-Za-z0-9][\w ._-]{0,40})[\"']?\s+(?:on|in)\s+(?:my\s+)?desktop",
        # create ... folder X  (but not "folder too/also/then")
        r"\b(?:create|make)\b.{0,20}\bfolder\b\s+[\"']?([A-Za-z0-9][\w ._-]{0,40})",
        # "new folder experiment"
        r"\bnew\s+folder\b\s+[\"']?([A-Za-z0-9][\w ._-]{0,40})",
    ]
    for pat in patterns:
        m = re.search(pat, low, flags=re.IGNORECASE)
        if m:
            name = m.group(1).strip().strip("\"'.").rstrip(".")
            # Drop trailing junk / conjunctions (and then open…)
            name = re.split(
                r"\s+(?:and|then|after|before|also|,)\b",
                name,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            name = re.sub(
                r"\s+(on|in|at|my|the|desktop|please|now)$",
                "",
                name,
                flags=re.IGNORECASE,
            ).strip()
            if name and name.lower() not in ban:
                return name
    return "New folder"


def create_desktop_folder(message: str) -> str:
    """Create a folder on the user's Desktop. Returns a short status reply."""
    desktop = get_desktop_path()
    name = _extract_folder_name(message)
    # Sanitize path segments
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .") or "New folder"
    path = desktop / name
    try:
        if path.exists():
            if path.is_dir():
                return f"Folder already exists on your Desktop: `{path}`"
            return f"A file already uses that name on Desktop: `{path}`"
        path.mkdir(parents=False, exist_ok=False)
        return f"Created folder on your Desktop: `{path}`"
    except Exception as exc:
        return f"Could not create folder `{path}`: {exc}"
