"""Start dev servers for the active project (frontend, backend, etc.)."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _spawn_detached(cmd: str, cwd: Path) -> subprocess.Popen:
    """Start a long-running process without blocking the assistant."""
    kwargs: dict = {
        "shell": True,
        "cwd": str(cwd),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


class ProjectRunner:
    """Detect stack and launch dev servers."""

    @staticmethod
    def detect_services(project_root: str | Path) -> dict[str, bool]:
        root = Path(project_root).resolve()
        pkg = root / "package.json"
        has_next = False
        if pkg.exists():
            try:
                import json
                data = json.loads(pkg.read_text(encoding="utf-8"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                has_next = "next" in deps or (root / "next.config.ts").exists() or (root / "next.config.js").exists()
            except Exception:
                has_next = True
        return {
            "frontend_next": has_next and pkg.exists(),
            "backend_fastapi": (root / "backend" / "main.py").exists(),
            "backend_python": (root / "main.py").exists() and not (root / "backend" / "main.py").exists(),
        }

    @staticmethod
    def start(project_root: str | Path) -> list[dict]:
        """Start detected dev servers. Returns metadata with URLs."""
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Project path not found: {root}")

        services = ProjectRunner.detect_services(root)
        started: list[dict] = []

        if services["backend_fastapi"]:
            backend_dir = root / "backend"
            cmd = (
                f'cd /d "{backend_dir}" && '
                f'"{sys.executable}" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000'
            )
            _spawn_detached(cmd, backend_dir)
            time.sleep(1.5)
            started.append({
                "service": "FastAPI backend",
                "url": "http://localhost:8000",
                "docs": "http://localhost:8000/docs",
                "health": "http://localhost:8000/health",
            })

        if services["backend_python"]:
            cmd = f'cd /d "{root}" && "{sys.executable}" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000'
            _spawn_detached(cmd, root)
            time.sleep(1.5)
            started.append({
                "service": "Python API",
                "url": "http://localhost:8000",
                "docs": "http://localhost:8000/docs",
            })

        if services["frontend_next"]:
            cmd = f'cd /d "{root}" && npm run dev'
            _spawn_detached(cmd, root)
            time.sleep(2.0)
            started.append({
                "service": "Next.js frontend",
                "url": "http://localhost:3000",
                "login": "http://localhost:3000/login",
            })

        return started
