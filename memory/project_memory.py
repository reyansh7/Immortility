"""Persistent project memory — remembers projects and their tech stacks.

Data is stored as JSON in ``memory/data/projects.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_projects_path() -> Path:
    try:
        from core.repo_paths import projects_memory_path

        return projects_memory_path()
    except Exception:
        return Path("memory/data/projects.json")


PROJECTS_FILE = str(_default_projects_path())


class ProjectMemory:
    """Stores and retrieves project profiles.

    Tracks:
    - Project name, path, language, framework, database
    - Architecture / important files / folder structure
    - Dependencies
    """

    def __init__(self, filepath: str | Path | None = None) -> None:
        self._path = Path(filepath) if filepath else _default_projects_path()
        self._projects: dict[str, dict[str, Any]] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._projects = json.loads(
                self._path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Corrupt project memory, starting fresh: %s", exc)
            self._projects = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._projects, indent=2), encoding="utf-8"
        )

    # ── Public API ──────────────────────────────────────────────────

    def remember_project(self, project_info: dict[str, Any]) -> None:
        """Store or update a project profile.

        Args:
            project_info: Dictionary with project metadata (from ProjectInfo.to_dict()).
        """
        name = project_info.get("name", "")
        if not name:
            logger.warning("Cannot remember project without a name.")
            return
        self._projects[name] = project_info
        self._save()
        logger.info("Project memory: remembered '%s'", name)

    def get_project(self, name: str) -> dict[str, Any] | None:
        """Retrieve a stored project profile by name."""
        return self._projects.get(name)

    def list_projects(self) -> list[str]:
        """Return all remembered project names."""
        return list(self._projects.keys())

    def forget_project(self, name: str) -> bool:
        """Remove a project from memory. Returns True if it existed."""
        if name in self._projects:
            del self._projects[name]
            self._save()
            logger.info("Project memory: forgot '%s'", name)
            return True
        return False

    def update_project(self, name: str, updates: dict[str, Any]) -> bool:
        """Merge updates into an existing project profile.

        Args:
            name: Project name.
            updates: Key-value pairs to merge.

        Returns:
            True if the project existed and was updated.
        """
        if name not in self._projects:
            return False
        self._projects[name].update(updates)
        self._save()
        logger.debug("Project memory: updated '%s'", name)
        return True

    def to_summary(self, name: str | None = None) -> str:
        """Format project info as a prompt-friendly summary.

        If *name* is given, summarise that project.
        Otherwise, list all known projects.
        """
        if name:
            info = self._projects.get(name)
            if not info:
                return ""
            parts = [f"Project: {info.get('name', name)}"]
            for key in ("language", "framework", "database", "package_manager", "project_type"):
                val = info.get(key)
                if val:
                    parts.append(f"  {key.replace('_', ' ').title()}: {val}")
            important = info.get("important_files", [])
            if important:
                parts.append(f"  Key Files: {', '.join(important[:10])}")
            return "\n".join(parts)

        # List all projects
        if not self._projects:
            return ""
        lines = ["Known projects:"]
        for pname, info in self._projects.items():
            fw = info.get("framework", "")
            lang = info.get("language", "")
            lines.append(f"  {pname}: {lang} {fw}".strip())
        return "\n".join(lines)

    def count(self) -> int:
        """Return number of remembered projects."""
        return len(self._projects)
