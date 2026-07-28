"""Safety locks for HUD confirmation + pending-action exact match."""

from __future__ import annotations

from core.pending_action import (
    CONFIRMATION_TOOLS,
    is_approval,
    is_rejection,
    needs_confirmation,
)
from core.repo_paths import get_repo_root, state_file_path, vector_db_dir


def test_mutating_tools_require_confirmation():
    for tool in (
        "write_file",
        "delete_file",
        "run_command",
        "edit_file",
        "apply_patch",
        "kill_process",
    ):
        assert tool in CONFIRMATION_TOOLS or needs_confirmation(tool, {"cmd": "echo hi"})
        assert needs_confirmation(
            tool,
            {"path": "x.py", "cmd": "echo hi", "pid": 1},
        )


def test_read_and_open_skip_confirmation():
    assert not needs_confirmation("read_file", {"path": "a.py"})
    assert not needs_confirmation("list_directory", {"path": "."})
    assert not needs_confirmation("open_url", {"url": "https://example.com"})
    assert not needs_confirmation("open_application", {"name": "chrome"})


def test_approval_is_exact_only():
    assert is_approval("yes")
    assert is_approval("confirm")
    assert not is_approval("yes delete it")
    assert not is_approval("ok go ahead and wipe")
    assert is_rejection("no")
    assert is_rejection("cancel")


def test_repo_paths_are_absolute_under_immortility():
    root = get_repo_root()
    assert root.name.lower().startswith("immortility")
    assert state_file_path().parent == root
    assert vector_db_dir().parent == root
