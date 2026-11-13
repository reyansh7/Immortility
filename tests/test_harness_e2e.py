"""End-to-End and Integration tests for ImmortalityHarness runtime.

Verifies:
1. Controlled mutation lifecycle (create -> edit -> 4-level verification -> complete).
2. Verification failure -> Recovery hints -> Retry repair -> Success.
3. Permission denial recovery (forbidden tool blocked -> recovery hint -> alternative tool -> success).
4. Task interruption, SQLite persistence verification, and resumption.
"""
import json
import pytest
from pathlib import Path

from core.task import TaskStatus
from core.task_manager import TaskManager
from core.task_db import TaskStore
from core.harness.contracts import BrainProvider
from core.harness.engine import ImmortalityHarness


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
        return json.dumps({"tool": "DONE", "args": {"message": "All scripted steps exhausted."}})


@pytest.fixture
def task_mgr(tmp_path):
    db_path = str(tmp_path / "e2e_tasks.db")
    store = TaskStore(db_path=db_path)
    return TaskManager(store=store)


@pytest.mark.asyncio
async def test_controlled_mutation_lifecycle_e2e(tmp_path, task_mgr):
    """Test full mutation lifecycle:

    1. inspect/read
    2. write initial file
    3. edit file
    4. verifier runs (mechanical syntax check passes)
    5. task completes with observations recorded.
    """
    target_file = tmp_path / "greeting.py"
    target_file.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    # Script:
    # Turn 1: read_file
    # Turn 2: edit_file (updating return value)
    # Turn 3: DONE
    script = [
        {
            "tool": "read_file",
            "args": {"path": str(target_file)},
        },
        {
            "tool": "edit_file",
            "args": {
                "path": str(target_file),
                "target_text": "    return 'hello'",
                "replacement_text": "    return 'hello world'",
            },
        },
        {
            "tool": "DONE",
            "args": {"message": "Updated greeting function successfully."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    res = await harness.run(
        "Update greeting function in greeting.py",
        require_edits=True,
        auto_confirm=True,
    )

    assert "Updated greeting function successfully." in res
    assert "hello world" in target_file.read_text(encoding="utf-8")

    # Verify task state in SQLite
    tasks = task_mgr.list_recent(limit=1)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.COMPLETED.value
    assert len(task.observations) >= 2
    # Verify verification was recorded
    assert task.verification is not None
    assert task.verification.passed
    assert task.verification.mechanical_passed


@pytest.mark.asyncio
async def test_mutation_recovery_from_syntax_error_e2e(tmp_path, task_mgr):
    """Test recovery loop when model introduces a syntax error:

    1. read_file
    2. edit_file introduces broken syntax ('def greet(:\n')
    3. model calls DONE -> Verifier catches Level 1 Mechanical failure
    4. Recovery hints injected: "Mechanical check failed... Syntax error"
    5. model calls write_file to repair the file with valid syntax
    6. model calls DONE -> Verifier passes -> Task completed!
    """
    target_file = tmp_path / "app.py"
    target_file.write_text("def run():\n    return True\n", encoding="utf-8")

    script = [
        {
            "tool": "read_file",
            "args": {"path": str(target_file)},
        },
        # Turn 2: broken edit
        {
            "tool": "edit_file",
            "args": {
                "path": str(target_file),
                "target_text": "def run():",
                "replacement_text": "def run(broken_syntax:",
            },
        },
        # Turn 3: premature DONE -> verifier will reject!
        {
            "tool": "DONE",
            "args": {"message": "Changes applied."},
        },
        # Turn 4: model repairs with write_file after seeing hint
        {
            "tool": "write_file",
            "args": {
                "path": str(target_file),
                "content": "def run():\n    return 42\n",
            },
        },
        # Turn 5: DONE -> now verifier passes!
        {
            "tool": "DONE",
            "args": {"message": "Syntax repaired successfully."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    res = await harness.run(
        "Refactor app.py safely",
        require_edits=True,
        auto_confirm=True,
    )

    assert "Syntax repaired successfully." in res
    assert "return 42" in target_file.read_text(encoding="utf-8")

    tasks = task_mgr.list_recent(limit=1)
    task = tasks[0]
    assert task.status == TaskStatus.COMPLETED.value
    assert len(task.errors) >= 1  # The verification error was logged
    assert task.verification.passed  # Final state passed


@pytest.mark.asyncio
async def test_permission_denial_recovery_e2e(tmp_path, task_mgr):
    """Test security boundary:

    1. Model attempts a mutation (write_file) in ASSISTED mode without confirmation
    2. Permission boundary flags it, task pauses in WAITING_PERMISSION
    3. User confirms via resume_confirmed
    4. Execution resumes, mutation completes, task finishes.
    """
    target_file = tmp_path / "config.json"

    # Turn 1: try to write file
    # Turn 2: DONE
    script = [
        {
            "tool": "write_file",
            "args": {"path": str(target_file), "content": '{"version": "2.1"}'},
        },
        {
            "tool": "DONE",
            "args": {"message": "Config written."},
        },
    ]

    brain = ScriptedBrain(script)
    harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

    # Run with auto_confirm=False to hit the security boundary
    paused_result = await harness.run(
        "Update config.json file",
        require_edits=True,
        auto_confirm=False,
    )

    assert "Proceed? (yes/no)" in paused_result
    # Check task is in WAITING_PERMISSION state
    tasks = task_mgr.list_recent(limit=1)
    assert tasks[0].status == TaskStatus.WAITING_PERMISSION.value

    # Now user confirms the action
    resumed_result = await harness.resume_confirmed(tasks[0].id)
    assert "Config written." in resumed_result

    # File now exists and has content
    assert target_file.exists()
    assert '"version": "2.1"' in target_file.read_text(encoding="utf-8")

    # Task is now COMPLETED
    updated_task = task_mgr.get_task(tasks[0].id)
    assert updated_task.status == TaskStatus.COMPLETED.value
