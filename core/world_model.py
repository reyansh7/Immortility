"""WorldModel — Structured environment and state representation for IMMORTALITY.

Aggregates:
  - OS / platform environment
  - Active project, root path, detected frameworks
  - Desktop / Projects structure
  - Active running tasks and background jobs
  - Safe, read-only state only (never mutates git or environment)
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.paths import get_desktop_path

logger = logging.getLogger(__name__)


@dataclass
class WorldModel:
    """Structured representation of the local environment and workspace state."""

    os_name: str
    desktop_path: str
    active_project_path: str = ""
    active_project_name: str = ""
    framework_hints: str = ""
    known_projects: list[str] = field(default_factory=list)
    recent_tasks: list[str] = field(default_factory=list)
    active_application: str = ""
    active_window: str = ""
    open_windows: list[str] = field(default_factory=list)
    screen_dimensions: tuple[int, int] = (1920, 1080)
    browser_state: dict[str, Any] = field(default_factory=dict)

    _instances: dict[str, WorldModel] = field(default_factory=dict)

    @classmethod
    def get_instance(cls, project_root: str = "") -> WorldModel:
        root_key = str(project_root or "")
        if root_key not in cls._instances:
            cls._instances[root_key] = cls.probe(project_root)
        return cls._instances[root_key]

    @classmethod
    def probe(cls, project_root: str = "") -> WorldModel:
        """Safely probe the local environment without mutating actions."""
        desktop = str(get_desktop_path())

        # Determine active project name
        proj_name = ""
        frameworks = ""
        if project_root:
            p = Path(project_root)
            proj_name = p.name
            try:
                from core.framework_hints import get_framework_hints
                frameworks = get_framework_hints(project_root) or ""
            except Exception:
                pass

        # Discover top projects
        known: list[str] = []
        try:
            from core.desktop_scanner import list_project_folders
            known = list_project_folders()[:8]
        except Exception:
            pass

        # Query recent active tasks safely
        recent_tasks_summary: list[str] = []
        try:
            from core.task_manager import get_task_manager
            active_tasks = get_task_manager().list_active()
            recent_tasks_summary = [f"{t.id[:8]} ({t.status}): {t.goal[:50]}" for t in active_tasks[:4]]
        except Exception:
            pass

        model = WorldModel(
            os_name=platform.system(),
            desktop_path=desktop,
            active_project_path=project_root,
            active_project_name=proj_name,
            framework_hints=frameworks,
            known_projects=known,
            recent_tasks=recent_tasks_summary,
        )
        model._bind_events()
        return model

    def _bind_events(self) -> None:
        """Subscribe to desktop and system events on EventBus."""
        try:
            from core.event_bus import EventBus
            EventBus().subscribe("computer.window_changed", self._on_window_changed)
            EventBus().subscribe("browser.navigated", self._on_browser_navigated)
        except Exception:
            pass

    def _on_window_changed(self, payload: dict[str, Any]) -> None:
        win = payload.get("window") or {}
        if win:
            title = win.get("title", "")
            self.active_window = title
            # Infer active app name from title
            if " - " in title:
                self.active_application = title.split(" - ")[0]
            elif "Visual Studio Code" in title:
                self.active_application = "Visual Studio Code"
            elif "Chrome" in title:
                self.active_application = "Google Chrome"
            else:
                self.active_application = title

    def _on_browser_navigated(self, payload: dict[str, Any]) -> None:
        self.browser_state = dict(payload)

    def get_summary(self) -> str:
        """Return a compact, token-budgeted representation for prompt injection."""
        lines = [f"OS: {self.os_name}"]
        if self.active_window:
            lines.append(f"Active Window: {self.active_window}")
        if self.active_application:
            lines.append(f"Active Application: {self.active_application}")
        if self.active_project_name:
            lines.append(f"Active Project: {self.active_project_name} ({self.active_project_path})")
        if self.framework_hints:
            lines.append(f"Framework: {self.framework_hints}")
        if self.known_projects:
            lines.append(f"Discovered Projects: {', '.join(self.known_projects[:5])}")
        if self.recent_tasks:
            lines.append("Active Tasks: " + "; ".join(self.recent_tasks))
        return "\n".join(lines)
