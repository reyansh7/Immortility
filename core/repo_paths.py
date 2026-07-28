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
