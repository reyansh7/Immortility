"""Comprehensive unit and integration test suite for the IMMORTALITY Custom Harness Runtime.

Tests:
  - ContextBuilder: selective retrieval and budgeting
  - HarnessPlanner: direct and multi-step plan generation
  - HarnessToolExecutor: safety boundaries, read-before-edit, permission gating, path normalization
  - HarnessVerifier: hierarchical 4-level verification and mechanical precedence
  - RecoveryManager: failure classification, bounded retries, identical failure halting, hints
  - HarnessAgentLoop & ImmortalityHarness: complete iterative reasoning cycle with MockBrainProvider
  - Task persistence & task resume from interrupted state
  - UserModel & WorldModel abstractions
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.harness.agent_loop import HarnessAgentLoop
from core.harness.brain_provider import MockBrainProvider
from core.harness.context_builder import ContextBuilder
from core.harness.contracts import FailureClass, ToolDecision
from core.harness.engine import ImmortalityHarness, is_simple_action
from core.harness.planner import HarnessPlanner
from core.harness.recovery import RecoveryManager
from core.harness.tool_executor import HarnessToolExecutor
from core.harness.verifier import HarnessVerifier
from core.task import Task, TaskStatus, VerificationResult
from core.task_db import TaskStore
from core.task_manager import TaskManager
from core.user_model import UserModel
from core.world_model import WorldModel


@pytest.fixture()
def tmp_store(tmp_path):
    db_path = tmp_path / "test_harness_tasks.db"
    store = TaskStore(db_path=db_path)
    yield store
    store._close()


@pytest.fixture()
def task_mgr(tmp_store):
    return TaskManager(store=tmp_store)


# ── ContextBuilder Tests ──────────────────────────────────────────────────────

class TestContextBuilder:
    def test_context_builder_budgeting(self):
        cb = ContextBuilder(max_context_chars=100, max_memory_chars=50)
        task = Task.create(goal="Refactor the auth layer")
        prompt = cb.build_system_prompt(
            task,
            tools_catalog="[tools list]",
            context_override="X" * 500,
            memory_context="M" * 300,
            require_edits=True,
        )
        assert "MODE: EDIT / MUTATE" in prompt
        assert "Refactor the auth layer" not in prompt  # Goal is in step prompt / plan
        assert "TOOL CATALOG" in prompt
        # Ensure budgeted fields are truncated
        assert "X" * 150 not in prompt

    def test_step_prompt_includes_recent_observations(self):
        cb = ContextBuilder()
        task = Task.create(goal="Find indexer bug")
        task_step_prompt = cb.build_step_prompt(
            task,
            current_step_index=0,
            recent_observations=[
                type("Obs", (), {"tool_name": "read_file", "result_summary": "def index(): pass"})()
            ],
        )
        assert "Find indexer bug" in task_step_prompt
        assert "read_file" in task_step_prompt
        assert "def index(): pass" in task_step_prompt


# ── Planner Tests ─────────────────────────────────────────────────────────────

class TestHarnessPlanner:
    def test_infer_direct_plan_readonly(self, task_mgr):
        planner = HarnessPlanner(task_manager=task_mgr)
        plan = planner.infer_direct_plan("Analyze the logging subsystem", require_edits=False)
        assert len(plan) == 1
        assert "logging" in plan[0][0].lower()

    def test_infer_direct_plan_edits(self, task_mgr):
        planner = HarnessPlanner(task_manager=task_mgr)
        plan = planner.infer_direct_plan("Fix the typo in main.py", require_edits=True)
        assert any("Verify" in step[0] for step in plan)

    @pytest.mark.asyncio
    async def test_llm_planning_with_mock_brain(self, task_mgr):
        planner = HarnessPlanner(task_manager=task_mgr)
        task = task_mgr.create_task("Complex multi-phase migration of database models across services")
        canned = json.dumps([
            {"description": "Inspect schema", "expected_outcome": "Schema identified"},
            {"description": "Apply migration", "expected_outcome": "Schema updated"},
        ])
        brain = MockBrainProvider(canned_responses=[canned])
        steps = await planner.plan_task(task, brain=brain, require_edits=True)
        assert len(steps) == 2
        assert steps[0].description == "Inspect schema"
        assert steps[1].expected_outcome == "Schema updated"


# ── ToolExecutor Tests ────────────────────────────────────────────────────────

class TestHarnessToolExecutor:
    def test_path_normalization(self, tmp_path):
        executor = HarnessToolExecutor(project_root=str(tmp_path), desktop_path=str(tmp_path))
        norm = executor.normalize_path("src/app.py")
        assert Path(norm).is_absolute()
        assert str(tmp_path).lower() in norm.lower()

    def test_read_before_edit_targeted_enforcement(self, tmp_path):
        test_file = tmp_path / "mod.py"
        test_file.write_text("x = 1\n", encoding="utf-8")

        executor = HarnessToolExecutor(project_root=str(tmp_path))
        # 1. Targeted edit before read -> MUST DENY
        decision = executor.evaluate_policy(
            "edit_file",
            {"path": str(test_file), "target_text": "x = 1", "replacement_text": "x = 2"},
            permission_mode="assisted",
        )
        assert decision.denied
        assert "must be inspected with read_file" in decision.reason

        # 2. create_file does NOT require prior read
        create_dec = executor.evaluate_policy(
            "create_file",
            {"path": str(tmp_path / "new.py"), "content": "y = 2\n"},
            permission_mode="autonomous",
        )
        assert not create_dec.denied

    def test_permission_modes(self):
        executor = HarnessToolExecutor()
        # SAFE mode denies mutating tools
        safe_dec = executor.evaluate_policy(
            "write_file",
            {"path": "x.py", "content": "hi"},
            permission_mode="safe",
        )
        assert safe_dec.denied

        # ASSISTED mode requires confirmation for mutations
        assist_dec = executor.evaluate_policy(
            "write_file",
            {"path": "x.py", "content": "hi"},
            permission_mode="assisted",
        )
        assert assist_dec.requires_confirmation

        # Read-only tools are allowed across all modes
        read_dec = executor.evaluate_policy(
            "read_file",
            {"path": "x.py"},
            permission_mode="safe",
        )
        assert read_dec.allowed


# ── Verifier Tests ────────────────────────────────────────────────────────────

class TestHarnessVerifier:
    @pytest.mark.asyncio
    async def test_mechanical_precedence_over_semantic(self, tmp_path):
        """Mechanical syntax failure CANNOT be overridden by LLM reviewer."""
        broken_py = tmp_path / "broken.py"
        broken_py.write_text("def broken(:\n    pass\n", encoding="utf-8")  # SyntaxError

        verifier = HarnessVerifier(project_root=str(tmp_path))
        # Brain falsely claims it's fine
        brain = MockBrainProvider(canned_responses=[json.dumps({"pass": True, "reason": "looks great"})])

        res = await verifier.verify(
            "fix syntax",
            touched_files=[str(broken_py)],
            before_snapshots={str(broken_py): "def old(): pass"},
            brain=brain,
            run_full_ci=False,
        )
        assert not res.passed
        assert not res.mechanical_passed
        assert "Mechanical" in res.details

    @pytest.mark.asyncio
    async def test_diff_passed_clean_file(self, tmp_path):
        clean_py = tmp_path / "clean.py"
        clean_py.write_text("def clean():\n    return 42\n", encoding="utf-8")

        verifier = HarnessVerifier(project_root=str(tmp_path))
        res = await verifier.verify(
            "add clean function",
            touched_files=[str(clean_py)],
            before_snapshots={str(clean_py): "def old(): pass"},
            run_full_ci=False,
        )
        assert res.mechanical_passed
        assert res.passed


# ── RecoveryManager Tests ─────────────────────────────────────────────────────

class TestRecoveryManager:
    def test_failure_classification(self):
        rm = RecoveryManager()
        assert rm.classify_failure("Target text not found in mod.py") == FailureClass.TARGET_NOT_FOUND.value
        assert rm.classify_failure("SyntaxError: invalid syntax") == FailureClass.SYNTAX_ERROR.value
        assert rm.classify_failure("Blocked in SAFE mode: write_file") == FailureClass.PERMISSION_DENIED.value
        assert rm.classify_failure("Operation timed out after 60s") == FailureClass.TIMEOUT.value

    def test_identical_failure_halts_loop(self, task_mgr):
        rm = RecoveryManager(max_identical_failures=2, task_manager=task_mgr)
        task = task_mgr.create_task("Fix bug")

        # First failure -> retry
        d1 = rm.evaluate(task, "syntax error on line 4", tool_name="edit_file", tool_args={"path": "a.py"})
        assert not d1.stop_execution
        assert d1.action == "retry"

        # Identical failure -> halt
        d2 = rm.evaluate(task, "syntax error on line 4", tool_name="edit_file", tool_args={"path": "a.py"})
        assert d2.stop_execution
        assert d2.action == "halt"
        assert "identically" in d2.reason

    def test_exceeded_retry_budget_triggers_rollback(self, task_mgr):
        rm = RecoveryManager(max_retries_per_class=2, max_identical_failures=5, task_manager=task_mgr)
        task = task_mgr.create_task("Refactor")
        # Record a fake checkpoint
        task_mgr.store.record_checkpoint(task.id, "fake_ckpt_dir")
        updated_task = task_mgr.get_task(task.id)

        rm.evaluate(updated_task, "syntax error 1", tool_name="edit_file")
        rm.evaluate(updated_task, "syntax error 2", tool_name="edit_file")
        d3 = rm.evaluate(updated_task, "syntax error 3", tool_name="edit_file")

        assert d3.stop_execution
        assert d3.action == "rollback"


# ── ImmortalityHarness End-to-End Tests ────────────────────────────────────────

class TestImmortalityHarness:
    @pytest.mark.asyncio
    async def test_harness_read_only_task_e2e(self, tmp_path, task_mgr):
        sample_file = tmp_path / "info.txt"
        sample_file.write_text("IMMORTALITY Architecture Version 2.0\n", encoding="utf-8")

        # Canned responses from Brain:
        # Turn 1: read_file
        # Turn 2: DONE with findings
        r1 = json.dumps({"tool": "read_file", "args": {"path": str(sample_file)}})
        r2 = json.dumps({"tool": "DONE", "args": {"message": "Architecture confirmed as version 2.0."}})

        brain = MockBrainProvider(canned_responses=[r1, r2])
        harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

        result = await harness.run(
            "Inspect architecture version",
            require_edits=False,
            auto_confirm=True,
        )

        assert "Architecture confirmed as version 2.0." in result
        assert task_mgr.list_recent(limit=5) == []

    def test_is_simple_action_skips_edits_and_heavy_goals(self):
        assert is_simple_action("open notepad", require_edits=False)
        assert is_simple_action("list files in this folder", require_edits=False)
        assert not is_simple_action("open notepad", require_edits=True)
        assert not is_simple_action("implement the logger and then refactor tests", require_edits=False)

    @pytest.mark.asyncio
    async def test_heavy_task_persists_to_sqlite(self, tmp_path, task_mgr):
        sample_file = tmp_path / "info.txt"
        sample_file.write_text("IMMORTALITY Architecture Version 2.0\n", encoding="utf-8")
        r1 = json.dumps({"tool": "read_file", "args": {"path": str(sample_file)}})
        r2 = json.dumps({"tool": "DONE", "args": {"message": "Architecture confirmed as version 2.0."}})
        brain = MockBrainProvider(canned_responses=[r1, r2])
        harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

        result = await harness.run(
            "implement the architecture inspector and then refactor it",
            require_edits=False,
            auto_confirm=True,
        )

        assert "Architecture confirmed as version 2.0." in result
        tasks = task_mgr.list_recent(limit=1)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.COMPLETED.value
        assert len(tasks[0].observations) >= 1
        assert tasks[0].observations[0].tool_name == "read_file"

    @pytest.mark.asyncio
    async def test_harness_interrupted_task_resume(self, tmp_path, task_mgr):
        sample_file = tmp_path / "data.txt"
        sample_file.write_text("Hello World\n", encoding="utf-8")

        # Create an interrupted task with an existing observation
        task = task_mgr.create_task("Read data and summarize")
        task_mgr.record_observation(
            task.id,
            step_index=0,
            tool_name="read_file",
            args={"path": str(sample_file)},
            result="Hello World",
            success=True,
        )
        task_mgr.mark_interrupted(task.id)

        # Brain generates completion directly based on resumed observation
        r_done = json.dumps({"tool": "DONE", "args": {"message": "Resumed and summarized: Hello World"}})
        brain = MockBrainProvider(canned_responses=[r_done])
        harness = ImmortalityHarness(task_manager=task_mgr, brain=brain, project_root=str(tmp_path))

        res = await harness.resume_task(task.id, auto_confirm=True)
        assert "Resumed and summarized: Hello World" in res
        resumed_task = task_mgr.get_task(task.id)
        assert resumed_task.status == TaskStatus.COMPLETED.value


# ── UserModel & WorldModel Tests ──────────────────────────────────────────────

class TestUserAndWorldModels:
    def test_user_model_summary(self):
        um = UserModel(
            name="Alice",
            role="Systems Engineer",
            preferred_language="rust",
            preferences={"editor": "vscode"},
        )
        summary = um.get_context_summary()
        assert "Alice" in summary
        assert "Systems Engineer" in summary
        assert "rust" in summary

    def test_world_model_probe(self, tmp_path):
        wm = WorldModel.probe(project_root=str(tmp_path))
        summary = wm.get_summary()
        assert "OS:" in summary
        assert str(tmp_path.name) in summary
