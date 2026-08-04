"""Resolve natural language project mentions to folder / TurboVec names."""

from __future__ import annotations

import re
from pathlib import Path


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def known_project_names() -> list[str]:
    """Desktop/Projects folders + names already in TurboVec."""
    names: list[str] = []
    seen: set[str] = set()

    def add(n: str) -> None:
        n = (n or "").strip()
        if not n or n in seen:
            return
        seen.add(n)
        names.append(n)

    try:
        from core.desktop_scanner import list_project_folders, projects_dir

        for n in list_project_folders():
            add(n)
        root = projects_dir()
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    add(child.name)
    except Exception:
        pass

    try:
        from rag.vector_store import VectorStore

        for n, _c in VectorStore().list_projects():
            add(n)
    except Exception:
        pass

    return names


def resolve_project_from_query(query: str, candidates: list[str] | None = None) -> str | None:
    """Match 'stocks app' / 'SkillLens' / etc. to a real project folder name."""
    q = (query or "").strip()
    if not q:
        return None
    cands = candidates if candidates is not None else known_project_names()
    if not cands:
        return None

    q_low = q.lower()
    q_norm = _norm(q)

    # Exact folder name as a whole word / path token
    for name in sorted(cands, key=len, reverse=True):
        if re.search(rf"(?i)\b{re.escape(name)}\b", q):
            return name

    # Normalized containment: "stocks app" <-> stocks_app
    best: str | None = None
    best_score = 0
    for name in cands:
        nn = _norm(name)
        if len(nn) < 3:
            continue
        score = 0
        if nn == q_norm or nn in q_norm:
            score = len(nn) + 10
        elif q_norm and q_norm in nn:
            score = len(q_norm)
        else:
            # token overlap: stocks + app vs stocks_app
            tokens = [t for t in re.split(r"[^a-z0-9]+", q_low) if len(t) > 2]
            hits = sum(1 for t in tokens if t in nn)
            if hits and hits == len(tokens):
                score = sum(len(t) for t in tokens) + 5
            elif hits >= 2:
                score = sum(len(t) for t in tokens if t in nn)
        if score > best_score:
            best_score = score
            best = name

    # Require a meaningful match (avoid matching tiny noise)
    if best and best_score >= 6:
        return best
    return None


def project_path_for_name(name: str) -> Path | None:
    """Absolute path under Desktop/Projects if it exists."""
    try:
        from core.desktop_scanner import projects_dir

        p = projects_dir() / name
        if p.is_dir():
            return p.resolve()
    except Exception:
        pass
    try:
        from core.paths import get_desktop_path

        p = get_desktop_path() / name
        if p.is_dir():
            return p.resolve()
    except Exception:
        pass
    return None
