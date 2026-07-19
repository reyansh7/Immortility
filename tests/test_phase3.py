"""Phase 3 workflow engine tests."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.checkpoint_manager import CheckpointManager
from core.decision_engine import DecisionEngine
from core.event_bus import EventBus
from core.workflow_state import WorkflowState, WorkflowStatus, WorkflowStep


@pytest.fixture(autouse=True)
def reset_event_bus():
    EventBus.reset()
    yield
    EventBus.reset()


def test_workflow_state_create_and_pause(tmp_path):
    db = tmp_path / "wf.db"
    state = WorkflowState(str(db))
    state.create_workflow("wf-1", WorkflowStep.PLANNING, {"goal": "test"})
    wf = state.get_workflow("wf-1")
    assert wf is not None
    assert wf["current_step"] == WorkflowStep.PLANNING
    state.pause_workflow("wf-1")
    assert state.get_workflow("wf-1")["paused"] is True


def test_decision_engine_pipeline():
    nxt = DecisionEngine.get_next_step(WorkflowStep.PLANNING, True)
    assert nxt == WorkflowStep.RESEARCH
    nxt = DecisionEngine.get_next_step(WorkflowStep.VERIFIER, False, retry_count=0)
    assert nxt == WorkflowStep.DEBUGGER


def test_checkpoint_manager_saves_files(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("x = 1\n", encoding="utf-8")
    mgr = CheckpointManager(tmp_path / "ckpts")
    ckpt = mgr.save("wf-1", {"goal": "test"}, [str(f)])
    assert Path(ckpt).exists()
    assert (Path(ckpt) / "context.json").exists()
    assert (Path(ckpt) / "manifest.json").exists()


def test_checkpoint_manager_rollback_restores_files(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("x = 1\n", encoding="utf-8")
    mgr = CheckpointManager(tmp_path / "ckpts")
    ckpt = mgr.save("wf-1", {"goal": "test"}, [str(f)])
    f.write_text("x = 999\n", encoding="utf-8")
    restored = mgr.rollback(ckpt)
    assert restored == [str(f)]
    assert f.read_text(encoding="utf-8") == "x = 1\n"


def test_decision_engine_patch_generator_to_debugger():
    nxt = DecisionEngine.get_next_step(WorkflowStep.PATCH_GENERATOR, False, retry_count=0)
    assert nxt == WorkflowStep.DEBUGGER


@pytest.mark.asyncio
async def test_core_verifier_verify_project_skips_build(tmp_path):
    from core.verifier import Verifier

    f = tmp_path / "app.py"
    f.write_text("x = 1\n", encoding="utf-8")
    v = Verifier(tmp_path)
    result = await v.verify_project([str(f)])
    assert result["success"] is True


@pytest.mark.asyncio
async def test_workflow_executor_passes_planner_hint(tmp_path):
    from core.workflow_executor import WorkflowExecutor
    from core.workflow_history import WorkflowHistory
    from core.workflow_state import WorkflowState

    state = WorkflowState(str(tmp_path / "wf.db"))
    history = WorkflowHistory(db_path=str(tmp_path / "wf.db"))
    executor = WorkflowExecutor(state, history)

    with patch("core.workflow_executor.CodingWorkflow") as mock_wf_cls:
        mock_wf = mock_wf_cls.return_value
        mock_wf.run = AsyncMock(
            return_value=type(
                "R",
                (),
                {"success": True, "message": "ok", "files_modified": [], "failed_patches": []},
            )()
        )
        await executor.run_coding_workflow(
            "wf-1", "add logging", str(tmp_path), "", planner_hint="read main.py first"
        )
        mock_wf.run.assert_awaited_once_with("add logging", planner_hint="read main.py first")


@pytest.mark.asyncio
async def test_workflow_engine_import_and_planning_step(tmp_path):
    from core.workflow_engine import WorkflowEngine

    db = tmp_path / "workflow.db"
    engine = WorkflowEngine(db_path=str(db))
    wf_id = engine.start_workflow({"goal": "Add logging", "project_root": str(tmp_path)})

    with patch("core.workflow_engine.agent_step", new_callable=AsyncMock) as mock_step:
        mock_step.return_value = "1. Add import\n2. Add log line"
        with patch.object(
            engine.executor, "run_coding_workflow", new_callable=AsyncMock
        ) as mock_coding:
            mock_coding.return_value = {
                "success": True,
                "message": "Done",
                "files_modified": [],
                "failed_patches": [],
            }
            with patch(
                "editing.edit_planner.EditPlanner.generate_plan", new_callable=AsyncMock
            ) as mock_plan:
                from editing.edit_planner import EditPlan

                mock_plan.return_value = EditPlan(
                    goal="Add logging",
                    files_to_read=[],
                    files_to_edit=[],
                    dependencies=[],
                    risks=[],
                    verification_strategy="",
                    symbols_to_modify=[],
                    plan_steps=[],
                )
                result = await engine.run_workflow(wf_id)

    assert result["workflow_id"] == wf_id
    assert result["status"] in ("completed", WorkflowStatus.COMPLETED.value)


def test_event_bus_publish():
    bus = EventBus()
    seen = []
    bus.subscribe("test.event", lambda p: seen.append(p))
    bus.publish("test.event", {"x": 1})
    assert seen == [{"x": 1}]
