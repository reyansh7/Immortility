"""Phase 4 — permission modes + session resume/handoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.control_plane import handle_control_command, is_control_command
from core.permissions import (
    ALLOW,
    CONFIRM,
    DENY,
    MODE_ASSISTED,
    MODE_AUTONOMOUS,
    MODE_DEVELOPER,
    MODE_SAFE,
    decide,
    reset_mode_for_tests,
)
from memory.session_resume import capture_handoff, format_handoff, write_handoff_file


@pytest.fixture(autouse=True)
def _reset_permission_mode():
    from core.permissions import get_mode, set_mode

    previous = get_mode()
    reset_mode_for_tests()
    yield
    reset_mode_for_tests()
    try:
        set_mode(previous)
    except Exception:
        pass


def test_assisted_matches_legacy_confirmation():
    write = decide("write_file", {"path": "x.py"}, mode=MODE_ASSISTED)
    assert write.action == CONFIRM
    read = decide("read_file", {"path": "x.py"}, mode=MODE_ASSISTED)
    assert read.action == ALLOW
    status = decide("git_status", {"cwd": "."}, mode=MODE_ASSISTED)
    assert status.action == ALLOW


def test_safe_denies_mutations_allows_reads():
    denied = decide("write_file", {"path": "x.py"}, mode=MODE_SAFE)
    assert denied.action == DENY
    assert denied.denied
    hard = decide("git_reset", {"mode": "hard"}, mode=MODE_SAFE)
    assert hard.action == DENY
    read = decide("read_file", {"path": "x.py"}, mode=MODE_SAFE)
    assert read.action == ALLOW


def test_autonomous_auto_approves_writes_but_not_destructive():
    write = decide("write_file", {"path": "x.py"}, mode=MODE_AUTONOMOUS)
    assert write.action == ALLOW
    add = decide("git_add", {"paths": ["x.py"]}, mode=MODE_AUTONOMOUS)
    assert add.action == ALLOW
    hard = decide("git_reset", {"mode": "hard"}, mode=MODE_AUTONOMOUS)
    assert hard.action == CONFIRM
    force = decide("git_push", {"force": True}, mode=MODE_AUTONOMOUS)
    assert force.action == CONFIRM
    unknown = decide("mystery_mutator", {"path": "x"}, mode=MODE_AUTONOMOUS)
    assert unknown.action == CONFIRM


def test_developer_allows_unknown_still_confirms_destructive():
    unknown = decide("mystery_mutator", {"path": "x"}, mode=MODE_DEVELOPER)
    assert unknown.action == ALLOW
    hard = decide("git_reset", {"mode": "hard"}, mode=MODE_DEVELOPER)
    assert hard.action == CONFIRM
    rm = decide("delete_file", {"path": "x.py"}, mode=MODE_DEVELOPER)
    assert rm.action == CONFIRM


def test_control_commands_recognized():
    assert is_control_command("/mode")
    assert is_control_command("/mode safe")
    assert is_control_command("/resume")
    assert is_control_command("/handoff")
    assert not is_control_command("/open C:\\proj")
    assert not is_control_command("mode safe")


def test_mode_command_roundtrip():
    shown = handle_control_command("/mode")
    assert shown is not None
    assert "assisted" in shown.lower() or "Permission mode" in shown
    changed = handle_control_command("/mode safe")
    assert changed is not None
    assert "safe" in changed.lower()
    bad = handle_control_command("/mode laser")
    assert bad is not None
    assert "Unknown mode" in bad


def test_handoff_format_and_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.repo_paths.handoff_path",
        lambda: tmp_path / "handoff.txt",
    )

    class Fake:
        permission_mode = "assisted"
        active_project = "demo"
        conversation_history = [
            {"role": "user", "content": "open the demo repo"},
            {"role": "assistant", "content": "Indexed demo."},
        ]
        pending_action = None
        pending_coding_request = {"request": "add login"}
        current_task = {"goal": "add login"}
        last_url = ""

    snap = capture_handoff(Fake())
    text = format_handoff(snap)
    assert "demo" in text
    assert "add login" in text
    assert "Permission mode" in text
    path = write_handoff_file(snap)
    assert path.is_file()
    assert "demo" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_kernel_denies_write_in_safe_mode():
    from core.execution_kernel import ExecutionKernel
    from core.permissions import set_mode

    set_mode("safe")
    kernel = ExecutionKernel()
    kernel.reset_cancel()
    result = await kernel.run_tool("write_file", {"path": "x.py", "content": "x"})
    assert not result.ok
    assert "denied" in (result.error or "")


def test_capability_report_includes_mode_card_does_not():
    from core.capabilities import capability_card, capability_report

    card = capability_card()
    assert "permission" not in card.lower()
    report = capability_report()
    assert "Permission mode:" in report
