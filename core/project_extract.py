"""Extract project paths and /open directives from natural-language input."""

from __future__ import annotations

import re
from pathlib import Path


_OPEN_RE = re.compile(r"/open\s+([^\s]+)", re.IGNORECASE)
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")


def _normalize_windows_path(raw: str) -> str:
    """Fix paths where \\r, \\n, \\t were eaten as escape sequences."""
    return (
        raw.replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .strip("\"'")
    )


def extract_open_path(user_input: str) -> tuple[str, str | None]:
    """
    Return (cleaned_message, project_path_or_none).
    Strips embedded /open <path> and standalone Windows paths used as project roots.
    """
    text = user_input.strip()
    project_path: str | None = None

    m = _OPEN_RE.search(text)
    if m:
        candidate = _normalize_windows_path(m.group(1))
        p = Path(candidate)
        if p.is_dir():
            project_path = str(p.resolve())
        text = (text[: m.start()] + text[m.end() :]).strip()

    if not project_path:
        for match in _WIN_PATH_RE.finditer(text):
            candidate = _normalize_windows_path(match.group(0))
            p = Path(candidate)
            if p.is_dir():
                project_path = str(p.resolve())
                break

    text = re.sub(r"\s+", " ", text).strip()
    return text, project_path


_WIN_PATH_FULL_RE = re.compile(
    r"([A-Za-z]:\\(?:[^/\n\r]+\\)*[^/\n\r\\]+)",
    re.IGNORECASE,
)


def extract_open_path_only(line: str) -> str | None:
    """Extract a Windows project path from an /open line (stops before extra words)."""
    line = line.strip()
    if not line.lower().startswith("/open"):
        return None

    _, embedded = extract_open_path(line)
    if embedded:
        return embedded

    raw = line[5:].strip().strip('"').strip("'")
    raw = raw.splitlines()[0].strip()
    m = _WIN_PATH_FULL_RE.match(raw)
    if m:
        candidate = _normalize_windows_path(m.group(1))
        p = Path(candidate)
        if p.is_dir():
            return str(p.resolve())

    first = raw.split()[0] if raw.split() else ""
    if first:
        candidate = _normalize_windows_path(first)
        p = Path(candidate)
        if p.is_dir():
            return str(p.resolve())
    return None


def is_run_project_request(text: str) -> bool:
    """True when the user wants to start/run the project, not edit code."""
    lower = text.strip().lower()
    phrases = (
        "run the code",
        "run the project",
        "run my project",
        "run project",
        "start the project",
        "start my project",
        "start project",
        "launch the project",
        "launch project",
        "run skilllens",
        "start skilllens",
        "run frontend",
        "run backend",
        "start frontend",
        "start backend",
        "start the frontend",
        "start the backend",
        "run both",
        "view the website",
        "open the website",
        "npm run dev",
        "start the server",
        "start dev server",
        "run dev server",
    )
    if any(p in lower for p in phrases):
        return True
    if lower.startswith("run ") and any(
        w in lower for w in ("project", "frontend", "backend", "website", "app", "server", "code")
    ):
        return True
    if lower.startswith("start ") and any(
        w in lower for w in ("project", "frontend", "backend", "website", "app", "server")
    ):
        return True
    return False


def is_implementation_confirm(text: str) -> bool:
    """True when the user is approving a proposed coding plan."""
    lower = text.strip().lower()
    if lower in {"yes", "y", "ok", "continue", "confirm", "proceed", "do it", "go ahead"}:
        return True
    phrases = (
        "make the changes",
        "make these changes",
        "implement these",
        "implement it",
        "yes make",
        "yes, make",
        "go ahead and",
        "please implement",
        "please make",
    )
    return any(p in lower for p in phrases)
