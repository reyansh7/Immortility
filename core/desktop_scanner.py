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


_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _desktop_and_projects_dirs() -> tuple[list[Path], list[Path]]:
    """Return (Desktop top-level dirs except Projects, Desktop/Projects children)."""
    desktop = get_desktop_path()
    top: list[Path] = []
    if desktop.is_dir():
        for p in desktop.iterdir():
            if not p.is_dir():
                continue
            if p.name.lower() in _SKIP_DESKTOP or p.name.lower() == "projects":
                continue
            top.append(p)
    proj_root = projects_dir()
    proj_children: list[Path] = []
    if proj_root.is_dir():
        for p in proj_root.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                proj_children.append(p)
    return sorted(top, key=lambda p: p.name.lower()), sorted(
        proj_children, key=lambda p: p.name.lower()
    )


def _resolve_folder_by_name(name: str) -> Path | None:
    n = _norm_name(name)
    if not n:
        return None
    top, proj_children = _desktop_and_projects_dirs()
    all_dirs = top + proj_children
    # Exact normalized match first
    for p in all_dirs:
        if _norm_name(p.name) == n:
            return p.resolve()
    # Containment fallback (e.g. "algo verse" -> AlgoVerse)
    for p in all_dirs:
        pn = _norm_name(p.name)
        if n in pn or pn in n:
            return p.resolve()
    return None


def resolve_scan_target(query: str) -> Path | None:
    """Resolve a user query to a concrete folder path for deterministic scan output."""
    q = (query or "").strip()
    if not q:
        return None

    # Explicit absolute Windows path in message wins
    for m in _WIN_PATH_RE.finditer(q):
        p = Path(m.group(0))
        if p.is_dir():
            return p.resolve()

    low = q.lower()
    top, proj_children = _desktop_and_projects_dirs()

    # Try to extract "analyze/scan/open <name>"
    m = re.search(
        r"\b(?:scan|analy[sz]e|tell me about|about|open|inspect|review)\s+([a-z0-9._\- ]{2,80})\b",
        low,
    )
    if m:
        cand = m.group(1)
        cand = re.sub(
            r"\b(on|in)\s+(my\s+)?desktop\b.*$", "", cand, flags=re.IGNORECASE
        )
        cand = re.sub(
            r"\b(folder|project|codebase|repo)\b", "", cand, flags=re.IGNORECASE
        ).strip()
        p = _resolve_folder_by_name(cand)
        if p is not None:
            return p

    # Fallback: any known folder token appearing in query
    for p in proj_children + top:
        if _norm_name(p.name) and _norm_name(p.name) in _norm_name(low):
            return p.resolve()

    # "scan number 2" support. Use only when we couldn't infer a concrete name.
    num = re.search(r"\b(?:number|no\.?|#)\s*(\d{1,3})\b", low)
    if num:
        idx = int(num.group(1))
        pool: list[Path] = []
        if "desktop" in low and "project" not in low:
            pool = top
        elif "project" in low:
            pool = proj_children
        else:
            # If ambiguous, use combined stable order: Projects then Desktop top.
            pool = proj_children + top
        if 1 <= idx <= len(pool):
            return pool[idx - 1].resolve()

    return None


def format_single_folder_report(path: Path) -> str:
    """Deterministic folder report from live filesystem only (no inference)."""
    p = path.resolve()
    if not p.is_dir():
        return f"Folder not found: `{p}`"

    kids = list(p.iterdir())
    dirs = sorted([k.name for k in kids if k.is_dir()], key=str.lower)
    files = sorted([k.name for k in kids if k.is_file()], key=str.lower)
    total_files = len(files)
    total_dirs = len(dirs)
    show_dirs = dirs[:16]
    show_files = files[:20]

    markers: list[str] = []
    if (p / "package.json").is_file():
        markers.append("Node/JavaScript project")
    if (p / "requirements.txt").is_file() or (p / "pyproject.toml").is_file():
        markers.append("Python project")
    if (p / ".git").is_dir():
        markers.append("git repo")
    if (p / "README.md").is_file():
        markers.append("README present")
    if (p / "data").is_dir() or (p / "dataset").is_dir():
        markers.append("has data folder")

    lines = [
        f"Folder: `{p.name}`",
        f"Path: `{p}`",
        f"Items: {len(kids)} total ({total_dirs} folders, {total_files} files).",
        "",
    ]
    if markers:
        lines.append(
            "Quick signal: " + ", ".join(markers) + "."
        )
        lines.append("")

    lines.append("Top-level folders:")
    if show_dirs:
        for d in show_dirs:
            lines.append(f"- {d}")
        if total_dirs > len(show_dirs):
            lines.append(f"- +{total_dirs - len(show_dirs)} more")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Top-level files:")
    if show_files:
        for f in show_files:
            lines.append(f"- {f}")
        if total_files > len(show_files):
            lines.append(f"- +{total_files - len(show_files)} more")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append(
        "This summary is from a live filesystem scan of this folder only; no guessed files."
    )
    return "\n".join(lines)


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
    # Index / open / deep-read intents must NEVER become a folder listing
    try:
        from tools.hud_knowledge import wants_index_or_ingest

        if wants_index_or_ingest(q):
            return False
    except Exception:
        pass
    if q.lower().startswith("/open"):
        return False
    if re.search(r"\b(index|ingest|reindex|re-index|embed)\b", q, re.I):
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
        # Still block if they asked to index/read deeply (belt-and-suspenders)
        if re.search(r"\b(index|ingest|read|understand|learn)\b", low):
            return False
        return True
    low = q.lower().replace("\\", "/")
    if "desktop/projects" in low or "desktop\\projects" in query.lower():
        if re.search(r"\b(index|ingest|/open|read|understand)\b", low):
            return False
        return True
    if "folder" in low and ("project" in low or "projects" in low):
        if re.search(r"\b(index|ingest|read|understand)\b", low):
            return False
        return True
    if "many other" in low or "not just" in low:
        return True
    # "analyze all projects on desktop" style — require "project"
    if "project" in low and "desktop" in low and any(
        w in low for w in ("list", "analy", "show", "what", "scan", "all")
    ):
        if re.search(r"\b(index|ingest|read|understand|learn)\b", low):
            return False
        return True
    if "analy" in low and "project" in low:
        if re.search(r"\b(index|ingest)\b", low):
            return False
        return True
    return False


def format_projects_report(*, deep: bool = False, include_desktop: bool = True) -> str:
    """Deterministic overview of Desktop/Projects — no RAG, no guessing.

    Default is a compact list (not a fat per-folder dump). Pass deep=True only
    when the user explicitly asks for structure/details inside each folder.
    """
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
            if deep:
                lines.append(f"{i}. **{name}**")
                detail = summarize_folder(proj / name)
                lines.append(f"   - {detail}")
                lines.append("")
            else:
                lines.append(f"{i}. {name}")

    if include_desktop:
        top = list_desktop_top_folders()
        lines.append("")
        lines.append(f"## Also on Desktop (outside Projects/) — {len(top)} folders")
        lines.append("")
        for i, name in enumerate(top, 1):
            lines.append(f"{i}. {name}")
        lines.append("")

    lines.append(
        "_Live filesystem scan — not inferred from memory._"
    )
    return "\n".join(lines)


def format_projects_compact(*, include_desktop: bool = True) -> str:
    """Short ground-truth inventory for LLM context / learning store."""
    proj = projects_dir()
    folders = list_project_folders(proj)
    lines = [
        f"Desktop/Projects path: {proj}",
        f"Project folder count: {len(folders)}",
        f"Projects: {', '.join(folders) if folders else '(none)'}",
    ]
    if include_desktop:
        top = list_desktop_top_folders()
        lines.append(f"Other Desktop folders ({len(top)}): {', '.join(top) if top else '(none)'}")
    return "\n".join(lines)


def wants_deep_projects_dump(query: str) -> bool:
    """True only when the user asks for structure/details inside folders."""
    low = (query or "").lower()
    return bool(
        re.search(
            r"\b(structure|detailed|details|overview of each|what.?s inside|"
            r"full list|tree|each folder|inside each)\b",
            low,
        )
    )


def answer_desktop_projects_query(
    user_query: str,
    *,
    include_desktop: bool | None = None,
    learn: bool = True,
) -> str:
    """Natural answer from a live scan — not a hardcoded fat markdown dump.

    Also stores a compact inventory snapshot into TurboVec so Immortility
    can recall it later via RAG.
    """
    low = (user_query or "").lower()
    if include_desktop is None:
        include_desktop = "desktop" in low or "all" in low

    compact = format_projects_compact(include_desktop=bool(include_desktop))
    folders = list_project_folders()
    desk = list_desktop_top_folders() if include_desktop else []

    if learn:
        try:
            from knowledge.learner import remember_desktop_inventory

            remember_desktop_inventory(
                f"User asked: {user_query}\nLive scan:\n{compact}"
            )
        except Exception:
            pass

    # Explicit full dump only when asked
    if wants_deep_projects_dump(user_query):
        return format_projects_report(deep=True, include_desktop=bool(include_desktop))

    # Pure yes/no / existence questions → deterministic short answer (no LLM bloat)
    if re.search(r"\b(any|are there|is there|do i have|how many)\b", low) and (
        "project" in low or "folder" in low
    ):
        n = len(folders)
        preview = ", ".join(folders[:6])
        extra = n - 6
        more = f", and {extra} more" if extra > 0 else ""
        desk_bit = ""
        if include_desktop and desk:
            desk_bit = (
                f" Also on the Desktop itself (outside Projects/): "
                f"{', '.join(desk[:5])}"
                + (f" +{len(desk) - 5} more." if len(desk) > 5 else ".")
            )
        if n == 0:
            return (
                "I scanned your Desktop/Projects folder live - it looks empty right now. "
                f"Path checked: `{projects_dir()}`."
                + (
                    f" On the Desktop top level I do see: {', '.join(desk[:7])}."
                    if desk
                    else ""
                )
            )
        return (
            f"Yes - there are {n} project folders in Desktop/Projects: "
            f"{preview}{more}."
            f"{desk_bit} "
            "Say if you want me to index any of them into my vector memory."
        )

    # Conversational answer grounded on live facts
    try:
        from core.llm import fast_chat
        from core.reply_format import polish_reply

        reply = fast_chat(
            user_query,
            extra_context=(
                "Live filesystem ground truth (do not invent folders):\n"
                f"{compact}\n\n"
                "Answer briefly and naturally in plain text. "
                "Do NOT dump a huge markdown inventory or per-folder file lists "
                "unless the user asked for details/structure. "
                "If they only asked whether projects exist, lead with yes/no and counts."
            ),
            max_output_tokens=220,
        )
        return polish_reply(reply) or spoken_projects_brief()
    except Exception:
        # Fallback without LLM
        if not folders:
            return f"No folders found under `{projects_dir()}`."
        return (
            f"Found {len(folders)} projects in Desktop/Projects: "
            f"{', '.join(folders)}."
        )


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
            f"and {extra} more."
        )
    return f"Your Desktop Projects folder has {n} folders: {preview}."


def whisper_vocabulary_hint() -> str:
    """Bias Whisper toward real project names on this machine."""
    names = list_project_folders() + list_desktop_top_folders()
    # Keep prompt short
    sample = ", ".join(names[:20])
    try:
        from memory.user_profile import get_user_name

        who = get_user_name()
    except Exception:
        who = "user"
    return (
        f"{who} Immortility coding assistant. "
        f"Project names: {sample}. "
        "Desktop Projects folder."
    )
