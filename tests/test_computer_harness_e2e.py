"""End-to-end integration tests proving Computer Use operating through ImmortalityHarness."""

import json
import pytest

from core.computer.controller import ComputerController
from core.computer.mock_backend import MockDesktopBackend
from core.harness.contracts import BrainProvider
from core.harness.engine import ImmortalityHarness
from core.task import TaskStatus
from core.task_db import TaskStore
from core.task_manager import TaskManager


class ScriptedBrain(BrainProvider):
    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.turn = 0

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        *,
        role: str = "brain",
        timeout_s: float = 60.0,
    ) -> str:
        if self.turn < len(self.script):
            resp = self.script[self.turn]
            self.turn += 1
            return json.dumps(resp)
        return json.dumps({"tool": "DONE", "args": {"message": "All scripted steps completed."}})


@pytest.fixture
def task_mgr(tmp_path):
    db_path = str(tmp_path / "computer_tasks.db")
    store = TaskStore(db_path=db_path)
    return TaskManager(store=store)


@pytest.fixture
def mock_backend():
    return MockDesktopBackend()


@pytest.fixture(autouse=True)
def setup_controller(mock_backend):
    ctrl = ComputerController(backend=mock_backend)
    ComputerController.set_instance(ctrl)
    yield ctrl
    ComputerController.set_instance(None)


@pytest.mark.asyncio
async def test_computer_use_open_app_e2e(tmp_path, task_mgr):
    """E2E Test 1:

    User: 'Open VS Code and inspect active window.'
    Flow:
      Harness -> Kimi/Brain -> computer_launch_app -> Permission -> Controller ->
      Observation -> Kimi/Brain -> computer_get_active_window -> Observation ->
      DONE -> Verification -> Completed.
    """
    script = [
        {
            "tool": "computer_launch_app",
            "args": {"name": "vscode"},
        },
        {
            "tool": "computer_get_active_window",
            "args": {},
        },
        {
            "tool": "DONE",
            "args": {"message": "Visual Studio Code is open and in focus."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    res = await harness.run(
        "Open VS Code app",
        require_edits=False,
        auto_confirm=True,
    )

    assert "Visual Studio Code is open and in focus." in res

    # Verify task persisted with all observations
    tasks = task_mgr.list_recent(limit=1)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.COMPLETED.value
    assert len(task.observations) >= 2

    # Verify observations record tool names
    tool_names = [o.tool_name for o in task.observations]
    assert "computer_launch_app" in tool_names
    assert "computer_get_active_window" in tool_names


@pytest.mark.asyncio
async def test_computer_use_open_forge_project_e2e(tmp_path, task_mgr):
    """E2E Test 2:

    User: 'Open my Forge project in VS Code.'
    Flow resolves project, launches application with path, verifies state.
    """
    from core.project_resolve import resolve_project_from_query

    matched_project = resolve_project_from_query("Open my Forge project in VS Code", candidates=["Forge", "SkillLens"])
    assert matched_project == "Forge"

    script = [
        {
            "tool": "computer_launch_app",
            "args": {"name": "vscode", "path": "Projects/Forge"},
        },
        {
            "tool": "computer_get_accessibility_tree",
            "args": {"max_depth": 2},
        },
        {
            "tool": "DONE",
            "args": {"message": "Forge project opened in Visual Studio Code with Explorer tree visible."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    res = await harness.run(
        "Open my Forge project in VS Code",
        require_edits=False,
        auto_confirm=True,
    )

    assert "Forge project opened" in res
    tasks = task_mgr.list_recent(limit=1)
    assert tasks[0].status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_computer_recovery_window_not_found_e2e(tmp_path, task_mgr):
    """E2E Test 3: Recovery loop on missing window:

    1. Brain attempts to focus a non-existent window
    2. Focus fails with WINDOW_NOT_FOUND
    3. Recovery hint injected: 'Window not found. Call computer_list_windows or computer_launch_app'
    4. Brain follows hint, calls computer_launch_app
    5. Task succeeds!
    """
    script = [
        # Turn 1: try to focus non-existent window
        {
            "tool": "computer_focus_window",
            "args": {"title": "MissingApp"},
        },
        # Turn 2: recover by launching app
        {
            "tool": "computer_launch_app",
            "args": {"name": "notepad"},
        },
        # Turn 3: finish
        {
            "tool": "DONE",
            "args": {"message": "Notepad launched after recovery."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    res = await harness.run(
        "Focus or open Notepad",
        require_edits=False,
        auto_confirm=True,
    )

    assert "Notepad launched after recovery." in res
    tasks = task_mgr.list_recent(limit=1)
    assert tasks[0].status == TaskStatus.COMPLETED.value
    # An error was recorded during recovery
    assert len(tasks[0].errors) >= 1
