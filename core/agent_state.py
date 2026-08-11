import json
import threading
from pathlib import Path

from core.research_context import ResearchContext
from core.repo_paths import state_file_path


def _max_history() -> int:
    try:
        from core.config import get_config

        return get_config().max_history
    except Exception:
        return 30


_SAVE_LOCK = threading.RLock()


class AgentState:
    """Singleton managing persistent state across user interactions."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._load()

    @property
    def state_path(self) -> Path:
        return state_file_path()

    def _load(self):
        self.mode = "CHAT"
        self.pending_action = None
        self.current_task = None
        self.active_project = None
        self.conversation_history: list[dict] = []
        self.research_context: dict | str | None = None
        self.browser_state = None
        self.pending_coding_request: dict | str | None = None
        self.chat_focus: dict | None = None

        path = self.state_path
        # Migrate legacy cwd-relative state.json once
        legacy = Path("state.json")
        if not path.is_file() and legacy.is_file() and legacy.resolve() != path.resolve():
            try:
                path.write_bytes(legacy.read_bytes())
            except OSError:
                pass

        if path.is_file():
            try:
                with _SAVE_LOCK:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                self.mode = data.get("mode", "CHAT")
                self.pending_action = data.get("pending_action")
                self.current_task = data.get("current_task")
                self.active_project = data.get(
                    "active_project", data.get("current_project")
                )
                self.conversation_history = data.get("conversation_history", [])
                self.research_context = data.get("research_context")
                self.browser_state = data.get("browser_state")
                self.pending_coding_request = data.get("pending_coding_request")
                self.chat_focus = data.get("chat_focus")
            except (json.JSONDecodeError, OSError):
                pass

    def reload(self) -> None:
        """Re-read state.json from disk (HUD / multi-process freshness)."""
        with _SAVE_LOCK:
            self._load()

    def get_research_context(self) -> ResearchContext | None:
        if isinstance(self.research_context, dict):
            return ResearchContext.from_dict(self.research_context)
        if isinstance(self.research_context, str) and self.research_context:
            return ResearchContext(extracted_text=self.research_context, summary="")
        return None

    def set_research_context(self, ctx: ResearchContext) -> None:
        self.research_context = ctx.to_dict()
        self.save()

    def cleanup_on_startup(self) -> None:
        """Clear stale in-flight tool confirmations; preserve project and conversation."""
        self.pending_action = None

        if self.current_task:
            step = self.current_task.get("step", "")
            workflow = self.current_task.get("workflow", "")
            if step in ("Executing", "Coding", "Planning", "Researching") or workflow == "leetcode":
                self.current_task = None

        max_h = _max_history()
        if len(self.conversation_history) > max_h:
            self.conversation_history = self.conversation_history[-max_h:]

        self.save()

    def update_browser_state(self, browser_state: dict) -> None:
        self.browser_state = browser_state
        self.save()

    def append_message(self, role: str, content: str) -> None:
        max_h = _max_history()
        with _SAVE_LOCK:
            self.conversation_history.append({"role": role, "content": content})
            if len(self.conversation_history) > max_h:
                self.conversation_history = self.conversation_history[-max_h:]
            # Persist under the same lock acquisition (BUG-9)
            data = {
                "mode": self.mode,
                "pending_action": self.pending_action,
                "current_task": self.current_task,
                "active_project": self.active_project,
                "conversation_history": self.conversation_history,
                "research_context": self.research_context,
                "browser_state": self.browser_state,
                "pending_coding_request": self.pending_coding_request,
                "chat_focus": self.chat_focus,
            }
            path = self.state_path
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(path)

    def save(self) -> None:
        data = {
            "mode": self.mode,
            "pending_action": self.pending_action,
            "current_task": self.current_task,
            "active_project": self.active_project,
            "conversation_history": self.conversation_history,
            "research_context": self.research_context,
            "browser_state": self.browser_state,
            "pending_coding_request": self.pending_coding_request,
            "chat_focus": self.chat_focus,
        }
        path = self.state_path
        tmp = path.with_suffix(".tmp")
        with _SAVE_LOCK:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(path)

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton — used in tests."""
        cls._instance = None
