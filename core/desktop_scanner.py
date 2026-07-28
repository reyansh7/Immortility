"""Live filesystem scans for Desktop / Projects — ground truth for the LLM."""

from __future__ import annotations

import re
from pathlib import Path

from core.paths import get_desktop_path

# Skip clutter when listing Desktop top-level
_SKIP_DESKTOP = {
    ".vscode",
    "__macosx",
    "graphify-out",
}


def projects_dir() -> Path:
    """Canonical Desktop\\Projects folder (case-insensitive on Windows)."""
    desktop = get_desktop_path()
    for name in ("Projects", "projects", "PROJECTS"):
        p = desktop / name
        if p.is_dir():
            return p.resolve()
    return (desktop / "Projects").resolve()


def list_project_folders(root: Path | None = None) -> list[str]:
    """Sorted directory names under Desktop/Projects."""
    root = root or projects_dir()
    if not root.is_dir():
        return []
    names = [p.name for p in root.iterdir() if p.is_dir()]
    return sorted(names, key=str.lower)


def list_desktop_top_folders() -> list[str]:
    """Top-level Desktop folders that look like projects (excluding Projects itself)."""
    desktop = get_desktop_path()
    out: list[str] = []
    for p in desktop.iterdir():
        if not p.is_dir():
            continue
        if p.name.lower() in _SKIP_DESKTOP or p.name.lower() == "projects":
            continue
        out.append(p.name)
    return sorted(out, key=str.lower)


def summarize_folder(path: Path, max_entries: int = 24) -> str:
    """One-line structural summary of a project folder."""
    if not path.is_dir():
        return "(missing)"
    kids = list(path.iterdir())
    dirs = sorted([c.name for c in kids if c.is_dir()], key=str.lower)[:12]
    files = sorted([c.name for c in kids if c.is_file()], key=str.lower)[:8]
    bits: list[str] = []
    if dirs:
        bits.append("dirs: " + ", ".join(dirs))
    if files:
        bits.append("files: " + ", ".join(files))
    extra = max(0, len(kids) - max_entries)
    if extra:
        bits.append(f"+{extra} more")
    return "; ".join(bits) if bits else "(empty)"


def build_projects_fact_block(*, include_desktop: bool = True, deep: bool = False) -> str:
    """Ground-truth text the model MUST treat as authoritative."""
    desktop = get_desktop_path()
    proj = projects_dir()
    folders = list_project_folders(proj)

    lines = [
        "=== LIVE FILESYSTEM SCAN (authoritative — do NOT invent or omit folders) ===",
        f"Desktop path: {desktop}",
        f"Projects path: {proj}",
        f"Projects folder exists: {proj.is_dir()}",
        f"Count of folders inside Projects: {len(folders)}",
        "Folders inside Desktop/Projects (complete list):",
    ]
    if folders:
        for i, name in enumerate(folders, 1):
            line = f"  {i}. {name}"
            if deep:
                line += f" — {summarize_folder(proj / name)}"
            lines.append(line)
    else:
        lines.append("  (none found — path may be wrong or empty)")

    if include_desktop:
        top = list_desktop_top_folders()
        lines.append(
            f"Also on Desktop (outside Projects/), {len(top)} other top-level folders:"
        )
        for i, name in enumerate(top, 1):
            lines.append(f"  D{i}. {name}")

    lines.append(
        "When asked to list/analyze the projects folder, answer ONLY from this scan. "
        "If the user says there are more, re-check this list — do not invent names "
        "from unrelated retrieval snippets."
    )
    lines.append("=== END LIVE SCAN ===")
    return "\n".join(lines)


_PROJECTS_QUERY = re.compile(
    r"("
    r"desktop\s*[/\\]\s*projects|"
    r"projects?\s+folder|folder[s]?\s+in\s+(the\s+)?projects|"
    r"list\s+(all\s+)?(the\s+)?(folders?|projects)|"
    r"analy[sz]e\s+.{0,40}projects?|"
    r"analy[sz]e\s+(the\s+)?(desktop[/\\]?)?projects?|"
    r"analy[sz]e\s+(the\s+)?desktop|"
    r"all\s+(the\s+)?projects?.{0,30}desktop|"
    r"projects?.{0,20}(on|in)\s+(my\s+)?desktop|"
    r"what\s+projects|"
    r"other\s+projects|"
    r"how\s+many\s+projects|"
    r"list\s+.{0,20}desktop|"
    r"folders?\s+on\s+(my\s+)?desktop|"
    r"scan\s+(my\s+)?desktop"
    r")",
    re.IGNORECASE,
)

# Writing/coding / creating folders on Desktop must NOT trigger a folder listing
_CODE_OR_FILE_INTENT = re.compile(
    r"("
    r"\b(write|create|save|make)\b.{0,40}\b(file|code|script|solution|python|\.py)\b|"
    r"\b(create|make|new)\b.{0,40}\bfolder\b|"
    r"\b(leetcode|class\s+Solution|two-letter|card\s+game)\b|"
    r"\b(fix|implement|refactor|debug)\b|"
    r"\bnew\s+file\b"
    r")",
    re.IGNORECASE,
)


def wants_projects_scan(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    # Coding / LeetCode / "write file on desktop" / "create folder" is NOT a listing
    if _CODE_OR_FILE_INTENT.search(q):
        return False
    low = q.lower()
    # "folder on desktop" alone is ambiguous — only list if clearly a scan/list ask
    if re.search(r"\bfolders?\s+on\s+(my\s+)?desktop\b", low):
        if not any(w in low for w in ("list", "show", "what", "scan", "how many", "analy")):
            return False
    if _PROJECTS_QUERY.search(q):
        return True
    low = q.lower().replace("\\", "/")
    if "desktop/projects" in low or "desktop\\projects" in query.lower():
        return True
    if "folder" in low and ("project" in low or "projects" in low):
        return True
    if "many other" in low or "not just" in low:
        return True
    # "analyze all projects on desktop" style — require "project"
    if "project" in low and "desktop" in low and any(
        w in low for w in ("list", "analy", "show", "what", "scan", "all")
    ):
        return True
    if "analy" in low and "project" in low:
        return True
    return False


def format_projects_report(*, deep: bool = True, include_desktop: bool = True) -> str:
    """Deterministic overview of Desktop/Projects — no RAG, no guessing."""
    desktop = get_desktop_path()
    proj = projects_dir()
    folders = list_project_folders(proj)

    lines = [
        f"# Desktop/Projects — live scan",
        f"",
        f"**Path:** `{proj}`",
        f"**Folder count:** {len(folders)}",
        f"",
        "## Folders inside Desktop/Projects",
        "",
    ]
    if not folders:
        lines.append("_No folders found (path missing or empty)._")
    else:
        for i, name in enumerate(folders, 1):
            lines.append(f"{i}. **{name}**")
            if deep:
                detail = summarize_folder(proj / name)
                lines.append(f"   - {detail}")
            lines.append("")

    if include_desktop:
        top = list_desktop_top_folders()
        lines.append(f"## Also on Desktop (outside Projects/) — {len(top)} folders")
        lines.append("")
        for i, name in enumerate(top, 1):
            lines.append(f"{i}. {name}")
        lines.append("")

    lines.append(
        "_This list is from a live filesystem scan. "
        "It is not a single open project (e.g. Resume Analyzer)._ "
    )
    return "\n".join(lines)


def spoken_projects_brief() -> str:
    """One short sentence for TTS about Desktop/Projects."""
    folders = list_project_folders()
    n = len(folders)
    if n == 0:
        return "I could not find any folders in your Desktop Projects directory."
    preview = ", ".join(folders[:5])
    extra = n - 5
    if extra > 0:
        return (
            f"Your Desktop Projects folder has {n} folders, including {preview}, "
            f"and {extra} more. The full list is on screen."
        )
    return f"Your Desktop Projects folder has {n} folders: {preview}."


def whisper_vocabulary_hint() -> str:
    """Bias Whisper toward real project names on this machine."""
    names = list_project_folders() + list_desktop_top_folders()
    # Keep prompt short
    sample = ", ".join(names[:20])
    return (
        "Reyansh Immortility coding assistant. "
        f"Project names: {sample}. "
        "Desktop Projects folder."
    )
