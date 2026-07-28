"""Shell command execution with PID tracking and safer defaults."""

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_bg_lock = threading.Lock()
_background_procs: dict[int, subprocess.Popen] = {}


def resolve_project_cwd() -> str | None:
    """Resolve active project directory for shell commands."""
    import os

    try:
        from knowledge.engine import KnowledgeEngine

        active = KnowledgeEngine().get_active_project()
        if active and active.path and os.path.isdir(active.path):
            return active.path
    except Exception:
        pass

    try:
        from core.agent_state import AgentState
        from memory.project_memory import ProjectMemory

        state = AgentState()
        if state.active_project:
            proj = ProjectMemory().get_project(state.active_project)
            path = (proj or {}).get("path", "")
            if path and os.path.isdir(path):
                return path
    except Exception:
        pass
    return None


def _should_use_shell(cmd: str) -> bool:
    """Use shell for pipelines, redirects, or Windows builtins."""
    if sys.platform == "win32":
        # PowerShell / cmd operators and builtins need a shell
        markers = ("|", ">", "<", "&&", "||", ";", "%", "$env:")
        if any(m in cmd for m in markers):
            return True
        first = cmd.strip().split(None, 1)[0].lower() if cmd.strip() else ""
        if first in {
            "dir", "echo", "cd", "copy", "move", "del", "type", "set",
            "cls", "mkdir", "rmdir", "start", "call",
        }:
            return True
        if first.endswith(".cmd") or first.endswith(".bat"):
            return True
    else:
        if any(m in cmd for m in ("|", ">", "<", "&&", "||", ";", "$")):
            return True
    return False


class CommandTool:
    """Tool for executing shell commands."""

    @staticmethod
    def list_background() -> list[dict[str, Any]]:
        with _bg_lock:
            dead = [pid for pid, p in _background_procs.items() if p.poll() is not None]
            for pid in dead:
                _background_procs.pop(pid, None)
            return [
                {"pid": pid, "returncode": p.poll()}
                for pid, p in _background_procs.items()
            ]

    @staticmethod
    def kill_background(pid: int) -> dict[str, Any]:
        with _bg_lock:
            proc = _background_procs.pop(int(pid), None)
        if not proc:
            return {"status": "error", "message": f"No tracked background PID {pid}"}
        try:
            proc.kill()
            proc.wait(timeout=3)
            return {"status": "success", "message": f"Killed PID {pid}"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def run_command(cmd: str, timeout: float = 60.0) -> dict:
        """Run a command; long-running processes stay tracked by PID."""
        try:
            if isinstance(timeout, str):
                timeout = float(timeout)

            cwd = resolve_project_cwd()
            use_shell = _should_use_shell(cmd)

            if use_shell:
                popen_args: Any = cmd
            else:
                try:
                    popen_args = shlex.split(cmd, posix=sys.platform != "win32")
                except ValueError:
                    popen_args = cmd
                    use_shell = True

            logger.info("run_command shell=%s cwd=%s :: %s", use_shell, cwd, cmd[:200])
            try:
                from rich.console import Console

                Console().print(f"[bold cyan][HUD/CLI shell][/bold cyan] {cmd}")
            except Exception:
                pass

            process = subprocess.Popen(
                popen_args,
                shell=use_shell,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return {
                    "status": "success",
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": process.returncode,
                    "pid": process.pid,
                }
            except subprocess.TimeoutExpired:
                with _bg_lock:
                    _background_procs[process.pid] = process
                return {
                    "status": "success",
                    "message": (
                        "Command still running in the background "
                        f"(PID {process.pid}). Use kill_process to stop it."
                    ),
                    "stdout": "(Output hidden because process is still running)",
                    "stderr": "",
                    "returncode": "RUNNING",
                    "pid": process.pid,
                }
        except Exception as e:
            return {"status": "error", "message": str(e)}
