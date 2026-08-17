"""HUD knowledge ops: /open indexing, bulk desktop ingest, deep project reads.

The HUD is the primary UI — these paths must actually run TurboVec ingest,
not fall through to folder listings or VS Code opens.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Rough wall-clock estimate for ETA messaging (embedding is the bottleneck)
_SEC_PER_PROJECT = 45.0
_SEC_PER_FILE = 0.35


def _is_slash_open(message: str) -> bool:
    return (message or "").strip().lower().startswith("/open")


def wants_index_or_ingest(message: str) -> bool:
    """True when the user wants RAG indexing / deep project understanding."""
    low = (message or "").strip().lower()
    if not low:
        return False
    if _is_slash_open(message):
        return True
    if re.search(
        r"\b(index|ingest|reindex|re-index|embed)\b.{0,60}\b"
        r"(project|projects|desktop|codebase|repo|folder|immortility)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(index|ingest)\b.{0,40}\b(them|everything|all)\b",
        low,
    ):
        return True
    # "read the projects", "understand my desktop projects", "learn my codebases"
    if re.search(
        r"\b(read|understand|learn|study|absorb|memorize|know)\b.{0,40}\b"
        r"(all\s+)?(the\s+)?(projects?|codebases?|repos?)\b",
        low,
    ):
        return True
    if re.search(
        r"\b(read|index|ingest).{0,30}\b(desktop|projects?\s+folder)\b",
        low,
    ):
        return True
    if "yourself" in low and re.search(r"\b(index|read|ingest)\b", low) and "project" in low:
        return True
    return False


def wants_listing_only(message: str) -> bool:
    """True for list/scan asks that should NOT trigger ingest."""
    low = (message or "").strip().lower()
    if wants_index_or_ingest(message):
        return False
    return bool(
        re.search(
            r"\b(list|show|how many|what projects|scan)\b",
            low,
        )
        and not re.search(r"\b(index|ingest|read|understand|learn)\b", low)
    )


def _estimate_eta_seconds(project_dirs: list[Path]) -> tuple[float, int]:
    """Return (eta_seconds, approx_file_count)."""
    files = 0
    for root in project_dirs:
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                # Cheap skip of heavy trees
                parts = {x.lower() for x in p.parts}
                if parts & {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build"}:
                    continue
                files += 1
                if files > 8000:
                    break
        except OSError:
            continue
    n = max(1, len(project_dirs))
    eta = max(n * 12.0, files * _SEC_PER_FILE, n * _SEC_PER_PROJECT * 0.4)
    # Cap the spoken ETA so we don't promise hours for a bad walk
    eta = min(eta, 45 * 60)
    return eta, files


def _format_duration(seconds: float) -> str:
    s = max(1, int(round(seconds)))
    if s < 60:
        return f"about {s} seconds"
    m, rem = divmod(s, 60)
    if m < 60:
        if rem >= 20:
            return f"about {m}–{m + 1} minutes"
        return f"about {m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"about {h}h {m}m"


def _resolve_open_path(message: str) -> Path | None:
    from core.project_extract import extract_open_path, extract_open_path_only

    if _is_slash_open(message):
        first = message.strip().splitlines()[0]
        raw = extract_open_path_only(first)
        if raw:
            return Path(raw)
    _, embedded = extract_open_path(message)
    if embedded:
        return Path(embedded)
    return None


def _is_projects_container(path: Path) -> bool:
    """True if path is Desktop/Projects (or similarly a multi-project parent)."""
    name = path.name.lower()
    if name in {"projects", "project"}:
        return True
    # Parent of many child code folders → treat as bulk root
    try:
        kids = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return False
    return len(kids) >= 3 and name in {"desktop", "code", "repos", "repositories", "dev"}


def _desktop_project_roots(*, include_desktop_top: bool = True) -> list[Path]:
    from core.desktop_scanner import list_desktop_top_folders, projects_dir
    from core.paths import get_desktop_path

    roots: list[Path] = []
    proj = projects_dir()
    if proj.is_dir():
        for name in sorted(
            [p.name for p in proj.iterdir() if p.is_dir()],
            key=str.lower,
        ):
            roots.append(proj / name)
    if include_desktop_top:
        desktop = get_desktop_path()
        skip = {"projects", "immortility1"}  # immortility indexed separately if asked
        for name in list_desktop_top_folders():
            if name.lower() in skip:
                continue
            p = desktop / name
            if p.is_dir():
                roots.append(p)
    return roots


def _index_single(path: Path, *, force: bool = False) -> str:
    from knowledge.engine import KnowledgeEngine
    from rag.bulk_ingestor import BulkIngestor

    try:
        from rich.console import Console

        console = Console()
    except Exception:
        console = None

    def progress(msg: str) -> None:
        if console is not None:
            console.print(msg, soft_wrap=True)
        else:
            print(msg, flush=True)
        import sys

        sys.stdout.flush()
        sys.stderr.flush()

    ke = KnowledgeEngine()
    existing = 0
    try:
        existing = ke._vector_store.count_by_project(path.name)  # noqa: SLF001
    except Exception:
        pass
    if existing > 0 and not force:
        progress(
            f"[yellow][HUD -> Index][/yellow] {path.name} already has "
            f"{existing} chunks - skipping (say 'reindex' to force)."
        )
        return (
            f"{path} is already in TurboVec ({existing} chunks). "
            f"Say reindex {path.name} or /open {path} with force/reindex "
            f"if you want a fresh pass."
        )

    eta, nfiles = _estimate_eta_seconds([path])
    progress(
        f"[bold cyan][HUD -> Index][/bold cyan] {path}  "
        f"ETA {_format_duration(eta)} (~{nfiles} files)"
    )
    ingestor = BulkIngestor(
        vector_store=ke._vector_store,  # noqa: SLF001
        embedding_model=ke._embedding_model,  # noqa: SLF001
        progress=progress,
    )
    t0 = time.perf_counter()
    result = ingestor.ingest_project(path)
    elapsed = time.perf_counter() - t0
    try:
        from core.agent_state import AgentState

        state = AgentState()
        state.active_project = result.name
        state.save()
    except Exception:
        pass
    try:
        ke._retriever.invalidate_cache()  # noqa: SLF001
        ke._retrieval_cache.invalidate_all()  # noqa: SLF001
    except Exception:
        pass

    if result.error:
        return f"Indexing `{path}` failed in {elapsed:.1f}s: {result.error}"

    progress(
        f"[bold green][HUD -> Index DONE][/bold green] {result.name} "
        f"{result.total_chunks} chunks in {elapsed:.1f}s"
    )
    return (
        f"Indexed {path} into TurboVec.\n\n"
        f"Done in {elapsed:.1f}s (estimate was {_format_duration(eta)}).\n\n"
        f"{result.name}: {result.total_files} files → "
        f"{result.total_chunks} chunks"
        + (f" ({result.language}/{result.framework})" if result.language else "")
        + "."
    )


def _index_many(
    roots: list[Path],
    *,
    force: bool = False,
    label: str = "Desktop projects",
) -> str:
    from knowledge.engine import KnowledgeEngine
    from rag.bulk_ingestor import BulkIngestor

    if not roots:
        return "I couldn't find any project folders to index on your Desktop."

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = str(r.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(r.resolve())
    roots = unique

    eta, nfiles = _estimate_eta_seconds(roots)
    try:
        from rich.console import Console

        console = Console()
    except Exception:
        console = None

    def progress(msg: str) -> None:
        if console is not None:
            console.print(msg, soft_wrap=True)
        else:
            print(msg, flush=True)
        import sys

        sys.stdout.flush()
        sys.stderr.flush()

    progress(
        f"[bold cyan][HUD -> Bulk Index][/bold cyan] {len(roots)} projects under {label}"
    )
    progress(
        f"[yellow]ETA {_format_duration(eta)}[/yellow] "
        f"(~{nfiles} files scanned for estimate). "
        f"force={force}. Watch this terminal for every project/file milestone."
    )
    for i, r in enumerate(roots, 1):
        progress(f"  queue {i}. {r.name}  ({r})")

    ke = KnowledgeEngine()
    # Reuse KE's stores so we don't load a second embedding model
    ingestor = BulkIngestor(
        vector_store=ke._vector_store,  # noqa: SLF001
        embedding_model=ke._embedding_model,  # noqa: SLF001
        progress=progress,
    )
    t0 = time.perf_counter()
    report = ingestor.ingest_paths(roots, force=force)

    # Invalidate caches after bulk work
    try:
        ke._retriever.invalidate_cache()  # noqa: SLF001
        ke._retrieval_cache.invalidate_all()  # noqa: SLF001
    except Exception:
        pass

    elapsed = time.perf_counter() - t0
    total_chunks_db = 0
    try:
        total_chunks_db = ke._vector_store.count()  # noqa: SLF001
    except Exception:
        pass

    lines = [
        f"Got it Reyansh — indexed {label} into TurboVec.",
        f"Finished in {elapsed:.1f}s (ETA was {_format_duration(eta)}).",
        "",
        "This run",
        f"- Newly indexed projects: {report.total_projects}",
        f"- Skipped (already had chunks): {len(report.skipped_projects)}",
        f"- Files touched: {report.total_files}",
        f"- New/updated chunks: {report.total_chunks}",
        f"- TurboVec total chunks now: {total_chunks_db}",
        "",
        "Per project",
        "",
    ]
    for r in report.projects:
        if r.skipped:
            lines.append(
                f"- {r.name} — skipped (already indexed, {r.total_chunks} chunks)"
            )
        elif r.error:
            lines.append(f"- {r.name} — FAILED: {r.error}")
        else:
            lines.append(
                f"- {r.name} — {r.total_files} files → {r.total_chunks} chunks "
                f"({r.time_seconds:.1f}s)"
                + (f" [{r.language}/{r.framework}]" if r.language or r.framework else "")
            )

    lines.append("")
    lines.append("Quick understanding (post-index)")
    lines.append("")
    try:
        from core.desktop_scanner import summarize_folder

        for root in roots[:16]:
            try:
                detail = summarize_folder(root)
                lines.append(f"- {root.name}: {detail}")
            except Exception:
                lines.append(f"- {root.name}: indexed")
    except Exception as exc:
        lines.append(f"Summary skim failed: {exc}")

    lines.append("")
    lines.append(
        "Ask me anything about these codebases now — I'll retrieve from TurboVec "
        "and read files when needed."
    )
    progress(
        f"[bold green][HUD -> Bulk Index DONE][/bold green] "
        f"{elapsed:.1f}s | DB chunks={total_chunks_db}"
    )
    return "\n".join(lines)


def handle_hud_knowledge(message: str) -> str | None:
    """Run index/open/deep-read knowledge ops. Returns reply or None."""
    msg = (message or "").strip()
    if not msg:
        return None

    force = bool(re.search(r"\b(reindex|re-index|force|again|fresh)\b", msg.lower()))

    # 1) Explicit /open <path>
    if _is_slash_open(msg) or wants_index_or_ingest(msg):
        path = _resolve_open_path(msg)
        if _is_slash_open(msg) and path is None:
            return (
                "Usage: /open <folder-path> — I index that folder into TurboVec "
                "(real RAG ingest), I do not only list names.\n"
                "Examples:\n"
                "- /open C:\\Users\\reyan\\OneDrive\\Desktop\\immortility1\n"
                "- /open C:\\Users\\reyan\\OneDrive\\Desktop\\Projects "
                "(indexes every project inside)\n"
                "- Or say: index my desktop projects / read the projects"
            )
        if path and path.is_dir():
            if _is_projects_container(path) or path.name.lower() == "projects":
                # Index children of Projects/
                kids = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
                return _index_many(kids, force=force, label=str(path))
            return _index_single(path, force=force)

        # No explicit path — bulk by phrase
        if wants_index_or_ingest(msg):
            low = msg.lower()
            include_top = bool(
                re.search(r"\ball\b", low)
                or (
                    "desktop" in low
                    and not re.search(r"desktop\s*[/\\]\s*projects|projects?\s+folder", low)
                )
                or re.search(r"\bdesktop\b.*\bprojects?\b|\bprojects?\b.*\bdesktop\b", low)
            )
            # "index projects" / "read the projects" → Desktop/Projects children
            # "index desktop" / "everything on desktop" → Projects + top-level folders
            if include_top and (
                "all" in low
                or re.search(r"\b(entire|whole|everything)\b", low)
                or ( "desktop" in low and "project" in low)
            ):
                roots = _desktop_project_roots(include_desktop_top=True)
                label = "Desktop/Projects + Desktop top folders"
            else:
                from core.desktop_scanner import projects_dir

                proj = projects_dir()
                roots = (
                    [p for p in proj.iterdir() if p.is_dir() and not p.name.startswith(".")]
                    if proj.is_dir()
                    else []
                )
                # Also include immortility1 if they said "yourself" / "immortility"
                if re.search(r"\b(yourself|immortility|own\s+code)\b", low):
                    from tools.self_inspect import project_root

                    roots.append(project_root())
                label = "Desktop/Projects"
            return _index_many(roots, force=force, label=label)

    return None
