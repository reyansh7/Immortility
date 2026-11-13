"""Tests for the IMMORTALITY Task persistence layer.

Covers:
  - Task creation and round-trip from SQLite
  - Status transitions
  - Observation recording
  - Error recording
  - Checkpoint / rollback linkage
  - Startup recovery detection
  - TaskManager lifecycle
  - execute_action native dispatch
  - config.py default backend value
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.task import (
    ErrorRecord,
    Observation,
    Task,
    TaskStatus,
    TaskStep,
    VerificationResult,
    _now_iso,
)
from core.task_db import TaskStore
from core.task_manager import TaskManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_store(tmp_path):
    """Fresh TaskStore backed by a temporary database."""
    db = tmp_path / "test_tasks.db"
    store = TaskStore(db_path=db)
    yield store
    store._close()


@pytest.fixture()
def manager(tmp_store):
    return TaskManager(store=tmp_store)


# ── Task model ────────────────────────────────────────────────────────────────

class TestTaskModel:
    def test_create_task(self):
        t = Task.create(goal="Fix failing tests")
        assert t.id
        assert t.goal == "Fix failing tests"
        assert t.status == TaskStatus.CREATED.value
        assert t.retries == 0
        assert t.observations == []
        assert t.plan == []

    def test_task_round_trip_json(self):
        t = Task.create(goal="Do something")
        d = t.to_dict()
        # plan/observations are JSON strings in the dict
        assert json.loads(d["plan"]) == []
        assert json.loads(d["observations"]) == []
        assert d["goal"] == "Do something"

    def test_task_from_row(self):
        t = Task.create(goal="Hello world")
        d = t.to_dict()
        t2 = Task.from_row(d)
        assert t2.id == t.id
        assert t2.goal == t.goal
        assert t2.status == t.status

    def test_task_summary_contains_id(self):
        t = Task.create(goal="A task")
        s = t.summary()
        assert t.id[:8] in s
        assert "CREATED" in s

    def test_is_active_terminal(self):
        t = Task.create(goal="x")
        assert t.is_active
        assert not t.is_terminal

    def test_task_step_lifecycle(self):
        s = TaskStep(index=0, description="Do it", expected_outcome="Done")
        assert s.status == TaskStatus.CREATED.value[
            :0
        ] or s.status  # just check it exists
        r = s.mark_running()
        assert r.status == "running"
        c = r.mark_completed()
        assert c.status == "completed"
        f = r.mark_failed("oops")
        assert f.status == "failed"
        assert f.error == "oops"


# ── TaskStore CRUD ────────────────────────────────────────────────────────────

class TestTaskStore:
    def test_create_and_get(self, tmp_store):
        t = Task.create(goal="Store test")
        tmp_store.create(t)
        loaded = tmp_store.get(t.id)
        assert loaded is not None
        assert loaded.id == t.id
        assert loaded.goal == "Store test"

    def test_get_missing_returns_none(self, tmp_store):
        assert tmp_store.get("nonexistent-id") is None

    def test_update_status(self, tmp_store):
        t = Task.create(goal="Status test")
        tmp_store.create(t)
        tmp_store.update_status(t.id, TaskStatus.EXECUTING)
        loaded = tmp_store.get(t.id)
        assert loaded.status == TaskStatus.EXECUTING.value

    def test_complete_task(self, tmp_store):
        t = Task.create(goal="Complete test")
        tmp_store.create(t)
        tmp_store.complete(t.id, final_result="All done.")
        loaded = tmp_store.get(t.id)
        assert loaded.status == TaskStatus.COMPLETED.value
        assert loaded.final_result == "All done."
        assert loaded.completed_at is not None

    def test_fail_task(self, tmp_store):
        t = Task.create(goal="Fail test")
        tmp_store.create(t)
        tmp_store.fail(t.id, reason="Something broke")
        loaded = tmp_store.get(t.id)
        assert loaded.status == TaskStatus.FAILED.value
        assert "Something broke" in loaded.final_result

    def test_record_observation(self, tmp_store):
        t = Task.create(goal="Obs test")
        tmp_store.create(t)
        tmp_store.record_observation(
            t.id,
            step_index=0,
            tool_name="read_file",
            args={"path": "test.py"},
            result='{"status":"success","result":{"content":"hello"}}',
            success=True,
        )
        loaded = tmp_store.get(t.id)
        assert len(loaded.observations) == 1
        obs = loaded.observations[0]
        assert obs.tool_name == "read_file"
        assert obs.success is True

    def test_record_multiple_observations(self, tmp_store):
        t = Task.create(goal="Multi obs")
        tmp_store.create(t)
        for i in range(3):
            tmp_store.record_observation(
                t.id,
                step_index=i,
                tool_name=f"tool_{i}",
                args={},
                result="ok",
                success=True,
            )
        loaded = tmp_store.get(t.id)
        assert len(loaded.observations) == 3

    def test_record_error(self, tmp_store):
        t = Task.create(goal="Error test")
        tmp_store.create(t)
        tmp_store.record_error(
            t.id,
            step_index=0,
            error_type="tool_failed",
            message="Permission denied",
            retry_number=1,
        )
        loaded = tmp_store.get(t.id)
        assert len(loaded.errors) == 1
        assert loaded.errors[0].error_type == "tool_failed"
        assert loaded.retries == 1

    def test_list_interrupted(self, tmp_store):
        t = Task.create(goal="Interrupt me")
        tmp_store.create(t)
        tmp_store.mark_interrupted(t.id)
        interrupted = tmp_store.list_interrupted()
        ids = [i.id for i in interrupted]
        assert t.id in ids

    def test_list_active_excludes_completed(self, tmp_store):
        t1 = Task.create(goal="Active")
        t2 = Task.create(goal="Done")
        tmp_store.create(t1)
        tmp_store.create(t2)
        tmp_store.update_status(t1.id, TaskStatus.EXECUTING)
        tmp_store.complete(t2.id, "finished")
        active = tmp_store.list_active()
        ids = [a.id for a in active]
        assert t1.id in ids
        assert t2.id not in ids

    def test_record_artifact(self, tmp_store):
        t = Task.create(goal="Artifact test")
        tmp_store.create(t)
        tmp_store.record_artifact(t.id, "core/auth.py")
        loaded = tmp_store.get(t.id)
        assert "core/auth.py" in loaded.artifacts

    def test_record_verification(self, tmp_store):
        t = Task.create(goal="Verify test")
        tmp_store.create(t)
        v = VerificationResult.create(passed=True, method="ci_gate", details="tests passed")
        tmp_store.record_verification(t.id, v)
        loaded = tmp_store.get(t.id)
        assert loaded.verification is not None
        assert loaded.verification.passed is True

    def test_resume_interrupted(self, tmp_store):
        t = Task.create(goal="Resume me")
        tmp_store.create(t)
        tmp_store.mark_interrupted(t.id)
        tmp_store.resume(t.id)
        loaded = tmp_store.get(t.id)
        assert loaded.status == TaskStatus.EXECUTING.value

    def test_duplicate_create_ignored(self, tmp_store):
        t = Task.create(goal="Dup test")
        tmp_store.create(t)
        tmp_store.create(t)  # second call should not raise
        assert tmp_store.get(t.id) is not None


# ── TaskManager ───────────────────────────────────────────────────────────────

class TestTaskManager:
    def test_create_task(self, manager):
        t = manager.create_task("Build a feature")
        assert t.id
        assert t.goal == "Build a feature"
        stored = manager.get_task(t.id)
        assert stored is not None
        assert stored.id == t.id

    def test_set_plan(self, manager):
        t = manager.create_task("Implement X")
        manager.set_plan(t.id, [
            ("Read current code", "Code is visible"),
            ("Edit file", "Changes applied"),
            ("Run tests", "Tests pass"),
        ])
        loaded = manager.get_task(t.id)
        assert len(loaded.plan) == 3
        assert loaded.plan[0].description == "Read current code"
        assert loaded.status == TaskStatus.PLANNING.value

    def test_begin_and_complete_step(self, manager):
        t = manager.create_task("Step lifecycle")
        manager.set_plan(t.id, [("Step 0", "Done")])
        manager.begin_step(t.id, 0)
        loaded = manager.get_task(t.id)
        assert loaded.plan[0].status == "running"
        assert loaded.status == TaskStatus.EXECUTING.value
        manager.complete_step(t.id, 0)
        loaded = manager.get_task(t.id)
        assert loaded.plan[0].status == "completed"

    def test_record_observation_via_manager(self, manager):
        t = manager.create_task("Manager obs")
        manager.record_observation(
            t.id,
            step_index=0,
            tool_name="git_status",
            args={"cwd": "."},
            result='{"status":"success","result":{}}',
            success=True,
        )
        loaded = manager.get_task(t.id)
        assert len(loaded.observations) == 1

    def test_complete_task_via_manager(self, manager):
        t = manager.create_task("Complete me")
        with patch("core.self_reflection.reflect_and_store"):
            manager.complete_task(t.id, "All done.")
        loaded = manager.get_task(t.id)
        assert loaded.status == TaskStatus.COMPLETED.value

    def test_startup_recover_finds_interrupted(self, manager):
        t = manager.create_task("Crash victim")
        manager.store.mark_interrupted(t.id)
        recovered = manager.startup_recover()
        assert any(r.id == t.id for r in recovered)

    def test_startup_recover_ignores_completed(self, manager):
        t = manager.create_task("Normal task")
        manager.complete_task(t.id, "done")
        recovered = manager.startup_recover()
        assert not any(r.id == t.id for r in recovered)

    def test_resume_task(self, manager):
        t = manager.create_task("Resume")
        manager.store.mark_interrupted(t.id)
        resumed = manager.resume_task(t.id)
        assert resumed is not None
        assert resumed.status == TaskStatus.EXECUTING.value

    def test_cancel_task(self, manager):
        t = manager.create_task("Cancel me")
        manager.cancel_task(t.id)
        loaded = manager.get_task(t.id)
        assert loaded.status == TaskStatus.CANCELLED.value

    def test_checkpoint_task(self, manager, tmp_path):
        t = manager.create_task("Checkpoint")
        test_file = tmp_path / "test_file.py"
        test_file.write_text("print('original')")
        with patch("core.checkpoint_manager.CheckpointManager.save") as mock_save:
            mock_save.return_value = str(tmp_path / "ckpt")
            ckpt = manager.checkpoint_task(t.id, [str(test_file)])
            assert ckpt == str(tmp_path / "ckpt")
            loaded = manager.get_task(t.id)
            assert str(tmp_path / "ckpt") in loaded.checkpoints

    def test_checkpoint_empty_files_returns_none(self, manager):
        t = manager.create_task("No files")
        result = manager.checkpoint_task(t.id, [])
        assert result is None


# ── Config defaults ───────────────────────────────────────────────────────────

class TestConfigDefaults:
    def test_default_agent_backend_is_immortality(self, monkeypatch):
        monkeypatch.delenv("IMMORTILITY_AGENT_BACKEND", raising=False)
        from core.config import reset_config_cache, get_config
        reset_config_cache()
        cfg = get_config()
        assert cfg.agent_backend == "immortality"
        reset_config_cache()

    def test_agent_backend_env_override(self, monkeypatch):
        monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "hermes")
        from core.config import reset_config_cache, get_config
        reset_config_cache()
        cfg = get_config()
        assert cfg.agent_backend == "hermes"
        reset_config_cache()

    def test_legacy_alias_accepted(self, monkeypatch):
        monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "legacy")
        from core.config import reset_config_cache, get_config
        reset_config_cache()
        cfg = get_config()
        assert cfg.agent_backend == "legacy"
        reset_config_cache()

    def test_tasks_db_path_has_default(self):
        from core.config import get_config, reset_config_cache
        reset_config_cache()
        cfg = get_config()
        assert cfg.tasks_db_path == "tasks.db"
        reset_config_cache()


# ── action_engine dispatch ────────────────────────────────────────────────────

class TestActionEngineDispatch:
    @pytest.mark.asyncio
    async def test_default_backend_routes_to_native_loop(self, monkeypatch):
        monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "immortality")
        monkeypatch.delenv("HERMES_API_KEY", raising=False)
        from core.config import reset_config_cache
        reset_config_cache()

        with patch("core.action_engine._run_native_loop",
                   new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = "Done from native loop."
            from core.action_engine import execute_action
            result = await execute_action("check git status")

        assert result == "Done from native loop."
        mock_loop.assert_awaited_once()
        reset_config_cache()

    @pytest.mark.asyncio
    async def test_hermes_skipped_without_api_key(self, monkeypatch):
        monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "hermes")
        monkeypatch.delenv("HERMES_API_KEY", raising=False)
        from core.config import reset_config_cache
        reset_config_cache()

        with patch("core.action_engine._run_native_loop",
                   new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = "Fell through to native."
            from core.action_engine import execute_action
            result = await execute_action("do something")

        assert result == "Fell through to native."
        reset_config_cache()
