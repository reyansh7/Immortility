"""Single command-execution engine.

Git, Docker, and any other shell-backed primitive MUST call ``run_argv`` /
``run_command`` here. Do not add a second subprocess runner.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Sequence

logger = logging.getLogger(__name__)

_bg_lock = threading.Lock()
_background_procs: dict[int, subprocess.Popen] = {}

CLASS_READONLY = "readonly"
CLASS_MUTATING = "mutating"
CLASS_DESTRUCTIVE = "destructive"

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_OUTPUT = 200_000

# Explicitly destructive / irreversible patterns (lowercase haystack).
_DESTRUCTIVE_PATTERNS = (
    "rm -rf",
    "rm -fr",
    "rmdir /s",
    "del /s",
    "rd /s",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "reg delete",
    "remove-item -recurse",
    "git reset --hard",
    "git reset --force",
    "git push --force",
    "git push -f ",
    "git push -f",
    "git push --force-with-lease",
    "git branch --delete",
    "git branch -d",
    "git branch -d",
    "git clean -f",
    "git rebase",
    "git filter-branch",
    "git filter-repo",
    "git stash drop",
    "git stash clear",
    "docker rm",
    "docker rmi",
    "docker system prune",
    "docker volume rm",
    "docker container prune",
)

_MUTATING_PATTERNS = (
    "git add",
    "git commit",
    "git checkout",
    "git switch",
    "git stash",
    "git push",
    "git pull",
    "git fetch",
    "git merge",
    "git restore",
    "git reset",
    "git mv",
    "git rm",
    "git tag",
    "npm install",
    "pip install",
    "docker stop",
    "docker kill",
    "docker run",
    "docker exec",
    "mkdir",
    "move ",
    "mv ",
    "copy ",
    "cp ",
)


def _cmd_limits() -> tuple[float, int]:
    try:
        from core.config import get_config

        cfg = get_config()
        timeout = float(getattr(cfg, "cmd_timeout_seconds", DEFAULT_TIMEOUT_S))
        max_out = int(getattr(cfg, "cmd_max_output_chars", DEFAULT_MAX_OUTPUT))
        return timeout, max_out
    except Exception:
        return DEFAULT_TIMEOUT_S, DEFAULT_MAX_OUTPUT


def classify_command(cmd: str) -> str:
    """Classify a shell command: readonly / mutating / destructive."""
    compact = " ".join((cmd or "").lower().split())
    if not compact:
        return CLASS_READONLY
    tokens = compact.split()
    if tokens and tokens[0] == "git":
        cleaned: list[str] = []
        i = 0
        while i < len(tokens):
            # git -C <path> / -c key=value — skip so the subcommand is visible
            if tokens[i] == "-c" and i + 1 < len(tokens):
                i += 2
                continue
            cleaned.append(tokens[i])
            i += 1
        tokens = cleaned or tokens
    if tokens and tokens[0] in {"git", "docker"}:
        flags = {t for t in tokens[1:] if t.startswith("-")}
        sub = tokens[1] if len(tokens) > 1 and not tokens[1].startswith("-") else ""
        if tokens[0] == "git":
            if sub == "branch" and ({"-D", "-d", "--delete"} & flags or "-D" in tokens or "-d" in tokens):
                return CLASS_DESTRUCTIVE
            if sub == "push" and ({"--force", "-f", "--force-with-lease"} & flags):
                return CLASS_DESTRUCTIVE
            if sub == "reset" and ({"--hard", "--force"} & flags):
                return CLASS_DESTRUCTIVE
            if sub == "commit" and "--amend" in tokens:
                return CLASS_DESTRUCTIVE
            if sub == "clean" and ({"-f", "-fd", "-fx", "--force"} & flags or "-fd" in tokens):
                return CLASS_DESTRUCTIVE
            if sub in {"rebase", "filter-branch", "filter-repo"}:
                return CLASS_DESTRUCTIVE
            if sub == "stash" and any(t in tokens for t in ("drop", "clear")):
                return CLASS_DESTRUCTIVE
        if tokens[0] == "docker" and sub in {"rm", "rmi", "prune"}:
            return CLASS_DESTRUCTIVE
        if tokens[0] == "format":
            return CLASS_DESTRUCTIVE
    for pat in _DESTRUCTIVE_PATTERNS:
        if pat in compact:
            return CLASS_DESTRUCTIVE
    for pat in _MUTATING_PATTERNS:
        if pat in compact:
            return CLASS_MUTATING
    return CLASS_READONLY


def classify_argv(argv: Sequence[str]) -> str:
    return classify_command(subprocess.list2cmdline(list(argv)) if argv else "")


def resolve_project_cwd() -> str | None:
    """Resolve active project directory for shell commands."""
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


def _limit_text(text: str | None, max_chars: int) -> tuple[str, bool]:
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    if max_chars <= 0 or len(raw) <= max_chars:
        return raw, False
    omitted = len(raw) - max_chars
    return raw[:max_chars] + f"\n...[truncated {omitted} chars]", True


def _kill_process(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=3)
    except Exception:
        pass


def _resolve_cwd(cwd: str | None) -> tuple[str | None, dict | None]:
    if cwd is None or str(cwd).strip() == "":
        return resolve_project_cwd(), None
    path = os.path.abspath(os.path.expanduser(str(cwd)))
    if not os.path.isdir(path):
        return None, {
            "status": "error",
            "error_code": "INVALID_CWD",
            "message": f"cwd is not a directory: {path}",
            "cwd": path,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "cancelled": False,
        }
    return path, None


def _kernel_cancelled() -> bool:
    try:
        from core.execution_kernel import get_kernel

        return get_kernel().cancelled()
    except Exception:
        return False


def _record_command_trace(
    *,
    argv_or_cmd: str,
    total_ms: int,
    error: str = "",
    cancelled: bool = False,
    detail: str = "",
) -> None:
    try:
        from core.harness import TraceEvent, record

        record(
            TraceEvent(
                kind="command",
                tool="run_command",
                total_ms=total_ms,
                error=error[:300] if error else "",
                cancelled=cancelled,
                detail=(detail or argv_or_cmd)[:240],
            )
        )
    except Exception:
        pass


def _popen_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        try:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        except Exception:
            pass
    else:
        kwargs["start_new_session"] = True
    return kwargs


def execute_command(
    popen_args: Any,
    *,
    shell: bool,
    cwd: str | None = None,
    timeout: float | None = None,
    max_output: int | None = None,
    background: bool = False,
    env: dict[str, str] | None = None,
    display: str = "",
) -> dict[str, Any]:
    """THE subprocess path. Git/Docker/run_command all land here."""
    started = time.perf_counter()
    default_timeout, default_max = _cmd_limits()
    timeout_s = default_timeout if timeout is None else float(timeout)
    max_chars = default_max if max_output is None else int(max_output)
    if timeout_s <= 0:
        timeout_s = default_timeout

    resolved, cwd_err = _resolve_cwd(cwd)
    if cwd_err:
        _record_command_trace(
            argv_or_cmd=display or str(popen_args),
            total_ms=0,
            error=cwd_err["error_code"],
        )
        return cwd_err

    label = display or (popen_args if isinstance(popen_args, str) else " ".join(map(str, popen_args)))
    classification = classify_command(label)
    logger.info(
        "execute_command shell=%s cwd=%s class=%s :: %s",
        shell,
        resolved,
        classification,
        str(label)[:200],
    )
    try:
        from rich.console import Console

        Console().print(f"[bold cyan][HUD/CLI shell][/bold cyan] {str(label)[:300]}")
    except Exception:
        pass

    if _kernel_cancelled():
        result = {
            "status": "error",
            "error_code": "CANCELLED",
            "message": "Command cancelled before start.",
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "pid": None,
            "cwd": resolved,
            "timed_out": False,
            "cancelled": True,
            "truncated": False,
            "classification": classification,
        }
        _record_command_trace(argv_or_cmd=label, total_ms=0, error="CANCELLED", cancelled=True)
        return result

    try:
        process = subprocess.Popen(
            popen_args,
            shell=shell,
            cwd=resolved,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **_popen_kwargs(),
        )
    except FileNotFoundError as exc:
        total = int((time.perf_counter() - started) * 1000)
        result = {
            "status": "error",
            "error_code": "COMMAND_NOT_FOUND",
            "message": str(exc),
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
            "pid": None,
            "cwd": resolved,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
            "classification": classification,
        }
        _record_command_trace(argv_or_cmd=label, total_ms=total, error="COMMAND_NOT_FOUND")
        return result
    except Exception as exc:
        total = int((time.perf_counter() - started) * 1000)
        result = {
            "status": "error",
            "error_code": "EXEC_FAILED",
            "message": str(exc),
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
            "pid": None,
            "cwd": resolved,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
            "classification": classification,
        }
        _record_command_trace(argv_or_cmd=label, total_ms=total, error=str(exc)[:200])
        return result

    if background:
        with _bg_lock:
            _background_procs[process.pid] = process
        total = int((time.perf_counter() - started) * 1000)
        result = {
            "status": "success",
            "message": f"Started in background (PID {process.pid}). Use kill_process to stop it.",
            "stdout": "(Output hidden because process is running in the background)",
            "stderr": "",
            "returncode": "RUNNING",
            "pid": process.pid,
            "cwd": resolved,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
            "classification": classification,
        }
        _record_command_trace(argv_or_cmd=label, total_ms=total, detail="background")
        return result

    holder: dict[str, Any] = {}
    done = threading.Event()

    def _communicate() -> None:
        try:
            holder["out"] = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            holder["timeout"] = True
        except Exception as exc:
            holder["exc"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_communicate, daemon=True)
    worker.start()
    cancelled = False
    while not done.wait(0.15):
        if _kernel_cancelled():
            cancelled = True
            _kill_process(process)
            break

    worker.join(timeout=2.0)

    if cancelled or holder.get("timeout") or "exc" in holder:
        if not cancelled and holder.get("timeout"):
            _kill_process(process)
        # Drain pipes after kill so the communicator thread can finish.
        try:
            out, err = process.communicate(timeout=2)
        except Exception:
            out, err = holder.get("out") or ("", "")
            if not isinstance(out, str):
                out = ""
            if not isinstance(err, str):
                err = ""
        stdout, trunc_out = _limit_text(out, max_chars)
        stderr, trunc_err = _limit_text(err, max_chars)
        total = int((time.perf_counter() - started) * 1000)
        if cancelled:
            result = {
                "status": "error",
                "error_code": "CANCELLED",
                "message": "Command cancelled.",
                "stdout": stdout,
                "stderr": stderr,
                "returncode": process.returncode,
                "pid": process.pid,
                "cwd": resolved,
                "timed_out": False,
                "cancelled": True,
                "truncated": trunc_out or trunc_err,
                "classification": classification,
            }
            _record_command_trace(
                argv_or_cmd=label, total_ms=total, error="CANCELLED", cancelled=True
            )
            return result
        if holder.get("timeout"):
            result = {
                "status": "error",
                "error_code": "TIMEOUT",
                "message": f"Command exceeded timeout of {timeout_s}s and was killed.",
                "stdout": stdout,
                "stderr": stderr,
                "returncode": process.returncode,
                "pid": process.pid,
                "cwd": resolved,
                "timed_out": True,
                "cancelled": False,
                "truncated": trunc_out or trunc_err,
                "classification": classification,
            }
            _record_command_trace(argv_or_cmd=label, total_ms=total, error="TIMEOUT")
            return result
        total = int((time.perf_counter() - started) * 1000)
        result = {
            "status": "error",
            "error_code": "EXEC_FAILED",
            "message": str(holder.get("exc")),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": process.returncode,
            "pid": process.pid,
            "cwd": resolved,
            "timed_out": False,
            "cancelled": False,
            "truncated": trunc_out or trunc_err,
            "classification": classification,
        }
        _record_command_trace(argv_or_cmd=label, total_ms=total, error=str(holder.get("exc"))[:200])
        return result

    out, err = holder.get("out") or ("", "")
    stdout, trunc_out = _limit_text(out, max_chars)
    stderr, trunc_err = _limit_text(err, max_chars)
    total = int((time.perf_counter() - started) * 1000)
    result = {
        "status": "success",
        "stdout": stdout,
        "stderr": stderr,
        "returncode": process.returncode,
        "pid": process.pid,
        "cwd": resolved,
        "timed_out": False,
        "cancelled": False,
        "truncated": trunc_out or trunc_err,
        "classification": classification,
        "error_code": None,
        "message": "",
    }
    _record_command_trace(argv_or_cmd=label, total_ms=total)
    return result


class CommandTool:
    """Tool for executing shell commands — the only command engine."""

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
    def run_argv(
        argv: Sequence[str],
        timeout: float | None = None,
        cwd: str | None = None,
        max_output: int | None = None,
        background: bool = False,
    ) -> dict:
        """Run an argument vector with NO shell. Preferred by Git/Docker tools."""
        args = [str(a) for a in argv]
        if not args:
            return {
                "status": "error",
                "error_code": "EMPTY_ARGV",
                "message": "argv must be a non-empty list.",
                "stdout": "",
                "stderr": "",
                "returncode": None,
                "cwd": cwd,
                "timed_out": False,
                "cancelled": False,
            }
        display = subprocess.list2cmdline(args)
        return execute_command(
            args,
            shell=False,
            cwd=cwd,
            timeout=timeout,
            max_output=max_output,
            background=background,
            display=display,
        )

    @staticmethod
    def run_command(
        cmd: str,
        timeout: float = 60.0,
        cwd: str | None = None,
        max_output: int | None = None,
        background: bool = False,
    ) -> dict:
        """Run a command; the single engine used by the Tool Kernel."""
        try:
            if isinstance(timeout, str):
                timeout = float(timeout)
            if isinstance(background, str):
                background = background.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(max_output, str):
                max_output = int(max_output)

            use_shell = _should_use_shell(cmd)
            if use_shell:
                popen_args: Any = cmd
            else:
                try:
                    popen_args = shlex.split(cmd, posix=sys.platform != "win32")
                except ValueError:
                    popen_args = cmd
                    use_shell = True

            return execute_command(
                popen_args,
                shell=use_shell,
                cwd=cwd,
                timeout=timeout,
                max_output=max_output,
                background=background,
                display=cmd,
            )
        except Exception as e:
            return {
                "status": "error",
                "error_code": "EXEC_FAILED",
                "message": str(e),
                "stdout": "",
                "stderr": str(e),
                "returncode": None,
                "timed_out": False,
                "cancelled": False,
            }
