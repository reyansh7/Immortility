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


_WIN_PATH_RE = re.compile(
    r'(?:'
    r'["\']([A-Za-z]:[/\\][^"\']+)["\']|'
    r'\b([A-Za-z]:(?:[/\\][^\s"\'<>|?*,;]+)*[/\\]?)|'
    r'(~[/\\][^\s"\'<>|?*,;]+)'
    r')'
)


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


def wants_single_folder_scan(query: str) -> bool:
    """True only for an explicit directory listing, not advice about a project."""
    low = (query or "").lower().strip()
    if not low:
        return False
    if re.search(
        r"\b(how (can|do|should|to|would)|make it|unique|production[- ]ready|"
        r"improve|check out|what should i|best way|differentiate)\b",
        low,
    ):
        return False
    if re.search(r"\b(scan|analy[sz]e|list|dir)\b", low):
        return True
    if re.search(r"\b(show|inspect)\s+(the |this |my )?(folder|dir|directory|contents)\b", low):
        return True
    if re.search(r"\bwhat's in\b|\bwhats in\b", low):
        return True
    return False


def resolve_scan_target(query: str) -> Path | None:
    """Resolve a user query to a concrete folder path for deterministic scan output."""
    import os

    q = (query or "").strip()
    if not q:
        return None

    # Explicit absolute Windows path in message wins
    for m in _WIN_PATH_RE.finditer(q):
        raw = (m.group(1) or m.group(2) or m.group(3) or m.group(0) or "").strip().rstrip(".,;!?)\"'")
        if not raw:
            continue
        try:
            expanded = Path(os.path.expanduser(os.path.expandvars(raw)))
            if expanded.is_dir() or expanded.is_file() or expanded.parent.is_dir():
                return expanded.resolve()
        except Exception:
            pass

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
    if p.is_file():
        try:
            sz = p.stat().st_size
            sz_str = f"{sz / 1024:.1f} KB" if sz > 1024 else f"{sz} bytes"
            return f"File: `{p.name}`\nPath: `{p}`\nSize: {sz_str}\n(Live filesystem check)"
        except Exception as exc:
            return f"File: `{p.name}`\nPath: `{p}`\nError reading file: {exc}"
    if not p.is_dir():
        return f"Folder not found: `{p}`"

    try:
        kids = list(p.iterdir())
    except PermissionError:
        return f"Folder: `{p.name}`\nPath: `{p}`\n(Access denied: protected system folder)"
    except Exception as exc:
        return f"Folder: `{p.name}`\nPath: `{p}`\n(Could not read folder: {exc})"

    dirs: list[str] = []
    files: list[str] = []
    for k in kids:
        try:
            if k.is_dir():
                dirs.append(k.name)
            elif k.is_file():
                files.append(k.name)
        except (PermissionError, OSError):
            continue
    dirs = sorted(dirs, key=str.lower)
    files = sorted(files, key=str.lower)
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
        f"Folder: `{p.name or str(p)}`",
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
    r"scan\s+(all\s+)?(my\s+)?(pc|computer|system|machine|desktop|laptop|projects?)|"
    r"(pc|computer|system|machine|desktop|projects?)\s+scan|"
    r"(inspect|check|list|show)\s+(my\s+)?(pc|computer|system|machine)"
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
    if re.search(r"\bscan\s+(all\s+)?(my\s+)?(pc|computer|system|machine|desktop|laptop|projects?)\b", low):
        if not re.search(r"\b(index|ingest|read|understand|learn)\b", low):
            return True
    if re.search(r"\b(pc|computer|system|machine|desktop|projects?)\s+scan\b", low):
        if not re.search(r"\b(index|ingest|read|understand|learn)\b", low):
            return True
    if re.search(r"\b(inspect|check|list|show)\s+(my\s+)?(pc|computer|system|machine)\b", low):
        if not re.search(r"\b(index|ingest|read|understand|learn)\b", low):
            return True
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

    Also stores a compact inventory snapshot into TurboVec asynchronously so
    Immortility can recall it later via RAG without blocking the response.
    """
    low = (user_query or "").lower()
    if include_desktop is None:
        include_desktop = any(
            k in low for k in ("desktop", "all", "pc", "computer", "system", "machine")
        )

    compact = format_projects_compact(include_desktop=bool(include_desktop))
    folders = list_project_folders()
    desk = list_desktop_top_folders() if include_desktop else []

    if learn:
        try:
            import threading
            from knowledge.learner import remember_desktop_inventory

            threading.Thread(
                target=remember_desktop_inventory,
                args=(f"User asked: {user_query}\nLive scan:\n{compact}",),
                daemon=True,
                name="remember-desktop-inventory",
            ).start()
        except Exception:
            pass

    # Explicit full dump only when asked
    if wants_deep_projects_dump(user_query):
        return format_projects_report(deep=True, include_desktop=bool(include_desktop))

    # Explicit scan or inventory command (e.g. "scan my pc", "pc scan", "scan desktop", "scan computer")
    # Returns the structured live scan report immediately: 0 tokens, sub-millisecond, 100% accurate.
    if re.search(r"\b(scan|inventory)\b", low):
        return format_projects_report(deep=False, include_desktop=bool(include_desktop))

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
            max_output_tokens=180,
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


def wants_project_explanation(query: str) -> bool:
    """True when user asks to explain, describe, or understand a specific project or the active project."""
    low = (query or "").lower().strip()
    if not re.search(
        r"\b(explain|what\s+is|what\s+does|tell\s+me\s+about|describe|overview\s+of|summary\s+of)\b",
        low,
    ):
        return False
    # Check reference to the current project/repo/codebase
    if re.search(
        r"\b(the\s+project|this\s+project|the\s+repo|this\s+repo|the\s+codebase|this\s+codebase|what\s+the\s+project\s+is|what\s+this\s+is|about\s+it)\b",
        low,
    ):
        return True
    from core.project_resolve import resolve_project_from_query

    return bool(resolve_project_from_query(query))


def explain_project_from_disk(target: str | Path | None = None) -> str | None:
    """Deterministic explanation of a project from its live README, manifest, and structure."""
    proj_path: Path | None = None

    def _resolve_candidate(c: Any) -> Path | None:
        if not c:
            return None
        if isinstance(c, Path) and c.is_dir():
            return c
        s = str(c).strip()
        if not s:
            return None
        cand = Path(s)
        try:
            if cand.is_dir():
                return cand
        except Exception:
            pass
        from core.project_resolve import project_path_for_name

        p = project_path_for_name(s)
        if p and p.is_dir():
            return p
        if s.lower() == "immortility":
            p = project_path_for_name("immortility1")
            if p and p.is_dir():
                return p
        top, proj_kids = _desktop_and_projects_dirs()
        for kid in proj_kids + top:
            if _norm_name(kid.name) == _norm_name(s):
                return kid
        return None

    if target:
        proj_path = _resolve_candidate(target)

    if not proj_path or not proj_path.is_dir():
        try:
            from core.agent_state import AgentState

            st = AgentState()
            # 1. Check chat_focus path or title
            if isinstance(st.chat_focus, dict):
                proj_path = _resolve_candidate(st.chat_focus.get("path")) or _resolve_candidate(
                    st.chat_focus.get("title")
                )
            # 2. Check active_project
            if not proj_path and st.active_project:
                proj_path = _resolve_candidate(st.active_project)
            # 3. Check recent conversation history for explicit project path or mention
            if not proj_path and st.conversation_history:
                from core.project_resolve import resolve_project_from_query

                for msg in reversed(st.conversation_history[-10:]):
                    content = msg.get("content", "")
                    m = re.search(r"([A-Za-z]:\\[^\s\r\n\t]+)", content)
                    if m:
                        cand = _resolve_candidate(m.group(1).rstrip("."))
                        if cand:
                            proj_path = cand
                            break
                    pname = resolve_project_from_query(content)
                    if pname:
                        cand = _resolve_candidate(pname)
                        if cand:
                            proj_path = cand
                            break
        except Exception:
            pass

    if not proj_path or not proj_path.is_dir():
        return None

    name = proj_path.name
    readme_text = ""
    for rname in ("README.md", "readme.md", "README", "readme.txt"):
        rf = proj_path / rname
        if rf.is_file():
            try:
                readme_text = rf.read_text(encoding="utf-8", errors="replace").strip()
                break
            except Exception:
                pass

    pyproj = proj_path / "pyproject.toml"
    pkg_json = proj_path / "package.json"
    manifest_desc = ""
    if not readme_text:
        if pkg_json.is_file():
            try:
                import json

                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                manifest_desc = str(data.get("description") or "").strip()
            except Exception:
                pass
        elif pyproj.is_file():
            try:
                txt = pyproj.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'description\s*=\s*["\']([^"\']+)["\']', txt)
                if m:
                    manifest_desc = m.group(1).strip()
            except Exception:
                pass

    lines = [f"# {name}", f"**Path:** `{proj_path}`", ""]

    # Extract description from README
    if readme_text:
        non_headers: list[str] = []
        for line in readme_text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("#"):
                clean_h = line_str.lstrip("#").strip()
                if clean_h.lower() != name.lower() and len(clean_h) > 5:
                    non_headers.append(f"**{clean_h}**")
                continue
            non_headers.append(line_str)
            if len(non_headers) >= 6:
                break
        desc = "\n\n".join(non_headers[:5])
        lines.append(desc if desc else f"Project {name} with README present.")
    elif manifest_desc:
        lines.append(f"**Description:** {manifest_desc}")
    else:
        lines.append(f"Local project `{name}` ({summarize_folder(proj_path)})")

    # Add quick signals
    signals: list[str] = []
    if (proj_path / ".git").is_dir():
        signals.append("Git repository")
    if (proj_path / "requirements.txt").is_file() or pyproj.is_file():
        signals.append("Python")
    if pkg_json.is_file():
        signals.append("Node/JavaScript")
    if (proj_path / "data").is_dir():
        signals.append("Data directory present")
    if signals:
        lines.append("")
        lines.append(f"**Signals:** {', '.join(signals)}")

    lines.append("")
    lines.append("_Live filesystem inspection — 100% accurate, no hallucination._")
    return "\n".join(lines)
