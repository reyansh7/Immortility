import subprocess

def resolve_project_cwd() -> str | None:
    """Resolve active project directory for shell commands."""
    import os

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

    try:
        from knowledge.engine import KnowledgeEngine

        active = KnowledgeEngine().get_active_project()
        if active and active.path and os.path.exists(active.path):
            return active.path
    except Exception:
        pass
    return None


class CommandTool:
    """Tool for executing shell commands."""

    @staticmethod
    def run_command(cmd: str, timeout: float = 60.0) -> dict:
        """Runs a shell command and returns stdout/stderr. Handles long-running commands gracefully."""
        try:
            if isinstance(timeout, str):
                timeout = float(timeout)
                
            cwd = resolve_project_cwd()

            process = subprocess.Popen(
                cmd,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            try:
                # Wait for up to `timeout` seconds for short commands to finish
                stdout, stderr = process.communicate(timeout=timeout)
                return {
                    "status": "success",
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": process.returncode
                }
            except subprocess.TimeoutExpired:
                # For long-running commands (like npm run dev), it's still running
                return {
                    "status": "success",
                    "message": "Command started successfully and is running in the background (e.g., a dev server).",
                    "stdout": "(Output hidden because process is still running)",
                    "stderr": "",
                    "returncode": "RUNNING"
                }
        except Exception as e:
            return {"status": "error", "message": str(e)}
