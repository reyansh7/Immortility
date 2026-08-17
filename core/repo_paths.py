"""Canonical Immortility repo paths — never depend on process cwd."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def get_repo_root() -> Path:
    return _ROOT


def state_file_path() -> Path:
    return _ROOT / "state.json"


def vector_db_dir() -> Path:
    path = _ROOT / ".vector_db"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = _ROOT / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_state_db_path() -> Path:
    return vector_db_dir() / "index_state.db"


def memory_data_dir() -> Path:
    path = _ROOT / "memory" / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def preferences_path() -> Path:
    return memory_data_dir() / "preferences.json"


def conversations_path() -> Path:
    return memory_data_dir() / "conversations.json"


def projects_memory_path() -> Path:
    return memory_data_dir() / "projects.json"


def user_profile_path() -> Path:
    return memory_data_dir() / "user_profile.json"


def experience_db_path() -> Path:
    path = _ROOT / ".immortility"
    path.mkdir(parents=True, exist_ok=True)
    return path / "experience.json"


def outcomes_db_path() -> Path:
    path = _ROOT / ".immortility"
    path.mkdir(parents=True, exist_ok=True)
    return path / "outcomes.db"


def experience_dataset_path() -> Path:
    path = _ROOT / ".immortility"
    path.mkdir(parents=True, exist_ok=True)
    return path / "experience_dataset.jsonl"


def workflow_db_path() -> Path:
    return _ROOT / "workflow.db"


def main_log_path() -> Path:
    return logs_dir() / "immortility.log"


def events_log_path() -> Path:
    return logs_dir() / "immortility_events.jsonl"


def hud_todos_path() -> Path:
    return memory_data_dir() / "hud_todos.json"
