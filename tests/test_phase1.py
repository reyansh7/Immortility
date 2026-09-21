"""
Phase 1 success tests for Immortility.
Run with: pytest tests/test_phase1.py -v
"""

import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent_state import AgentState
from core.pending_action import is_approval, is_rejection, needs_confirmation
from core.router import classify_route
from core.action_engine import is_summarize_request, is_page_query
from tools.tool_registry import ToolRegistry, setup_registry
from tools.file_tool import FileTool


@pytest.fixture(autouse=True)
def reset_singletons(tmp_path, monkeypatch):
    """Fresh state and temp desktop for each test."""
    AgentState.reset_instance()
    ToolRegistry.reset_instance()
    monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "legacy")
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    from core.config import reset_config_cache
    reset_config_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.txt").write_text("You are Immortility.", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.txt").write_text("", encoding="utf-8")
    yield
    reset_config_cache()
    AgentState.reset_instance()
    ToolRegistry.reset_instance()


# ── Test 1: Chat ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_1_chat_greeting():
    """User: hi → normal chat response."""
    with patch("core.action_engine._kernel_chat") as mock_chat, patch("core.llm.chat", mock_chat):
        mock_chat.return_value = {"message": {"content": "Hello! How can I help you today?"}}

        with patch("core.router.chat") as mock_route:
            mock_route.return_value = {"message": {"content": "CHAT"}}

            route = await classify_route("hi")
            assert route == "CHAT"

        from core.action_engine import agent_step

        response = await agent_step("Chat Assistant", "hi", persist_history=True)
        assert "Hello" in response
        state = AgentState()
        assert len(state.conversation_history) >= 2


# ── Test 2: Open URL ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_2_open_youtube():
    """User: open youtube → browser opens YouTube."""
    with patch("core.router.chat") as mock_route:
        mock_route.return_value = {"message": {"content": "ACTION"}}
        route = await classify_route("open youtube")
        assert route == "ACTION"

    with patch(
        "tools.browser_tool.BrowserTool.open_url",
        new_callable=AsyncMock,
    ) as mock_open:
        mock_open.return_value = {
            "status": "success",
            "url": "https://youtube.com",
            "message": "Successfully navigated",
        }
        ToolRegistry.reset_instance()
        registry = setup_registry()
        result = await registry.execute("open_url", {"url": "https://youtube.com"})
        data = json.loads(result)
        assert data["status"] == "success"
        mock_open.assert_called_once_with(url="https://youtube.com")


# ── Test 3: File creation with confirmation ─────────────────────────────────


def test_3_file_creation_confirmation_flow(tmp_path):
    """create hello.py → confirmation → yes → file created."""
    setup_registry()
    state = AgentState()
    target = tmp_path / "hello.py"

    assert needs_confirmation("create_file", {"path": str(target), "content": "print('hi')"})

    state.pending_action = {
        "tool": "create_file",
        "args": {"path": str(target), "content": "print('hi')"},
    }
    state.save()

    assert is_approval("yes")
    assert not is_rejection("yes")

    result = FileTool.create_file(str(target), "print('hi')")
    assert result["status"] == "success"
    assert target.read_text(encoding="utf-8") == "print('hi')"


# ── Test 4: Current page URL ────────────────────────────────────────────────


def test_4_page_query_detection():
    """what page am I on? → detected as page query."""
    assert is_page_query("what page am I on?")
    assert is_page_query("what's the current url?")


@pytest.mark.asyncio
async def test_4_get_current_url():
    setup_registry()
    registry = ToolRegistry()

    with patch(
        "tools.browser_tool.BrowserManager"
    ) as mock_mgr_cls:
        mock_page = MagicMock()
        mock_page.url = "https://youtube.com"
        mock_mgr = MagicMock()
        mock_mgr.get_page = AsyncMock(return_value=mock_page)
        mock_mgr.sync_state = AsyncMock()
        mock_mgr_cls.return_value = mock_mgr

        result = await registry.execute("get_current_url", {})
        data = json.loads(result)
        assert data["result"]["url"] == "https://youtube.com"


# ── Test 5: Summarize webpage ───────────────────────────────────────────────


def test_5_summarize_detection():
    assert is_summarize_request("summarize this webpage")


@pytest.mark.asyncio
async def test_5_summarize_webpage():
    with patch("core.action_engine.chat") as mock_chat:
        mock_chat.return_value = {"message": {"content": "This page is about videos."}}

        setup_registry()
        with patch(
            "tools.scraper_tool.BrowserManager"
        ) as mock_mgr_cls:
            mock_page = MagicMock()
            mock_page.url = "https://example.com"
            mock_page.evaluate = AsyncMock(return_value="Example page content about videos.")
            mock_mgr = MagicMock()
            mock_mgr.get_page = AsyncMock(return_value=mock_page)
            mock_mgr.sync_state = AsyncMock()
            mock_mgr_cls.return_value = mock_mgr

            from core.action_engine import handle_summarize_webpage

            summary = await handle_summarize_webpage()
            assert "videos" in summary.lower() or "page" in summary.lower()


# ── Test 6: LeetCode workflow ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_6_leetcode_workflow_pending_confirmation(tmp_path, monkeypatch):
    """LeetCode: research → solution → pending confirmation → save on yes."""
    monkeypatch.setattr("skills.leetcode_skill.get_desktop_path", lambda: tmp_path)

    sample_problem = {
        "title": "Two Sum",
        "id": "1",
        "difficulty": "Easy",
        "url": "https://leetcode.com/problems/two-sum/",
        "description": "Find two numbers that add up to target.",
        "examples": "nums = [2,7], target = 9",
        "tags": ["Array"],
        "hints": [],
        "snippet": "class Solution:\n    def twoSum(self, nums, target):",
    }

    with patch("agents.research_agent.get_daily_challenge", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = sample_problem

        with patch("skills.leetcode_skill.fetch_solution_from_web", new_callable=AsyncMock) as mock_web:
            mock_web.return_value = (
                "class Solution:\n    def twoSum(self, nums, target):\n        return [0, 1]"
            )

            with patch("core.action_engine._kernel_chat") as mock_chat:
                mock_chat.return_value = {"message": {"content": "Use a hash map."}}

                from skills.leetcode_skill import LeetCodeSkill

                skill = LeetCodeSkill()
                msg = await skill.run("solve today's leetcode and save it to desktop")

                state = AgentState()
                assert state.pending_action is not None
                assert state.pending_action["tool"] == "create_file"
                assert "leetcode_1_two_sum.py" in state.pending_action["args"]["path"]
                assert "class Solution" in state.pending_action["args"]["content"]
                assert "Proceed" in msg or "create" in msg.lower()

                path = Path(state.pending_action["args"]["path"])
                content = state.pending_action["args"]["content"]
                FileTool.create_file(str(path), content)
                assert path.exists()
                assert "Two Sum" in path.read_text(encoding="utf-8")


# ── Pending action priority ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_action_checked_before_routing():
    """'yes' with pending action should be approval, not new request."""
    state = AgentState()
    state.pending_action = {"tool": "create_file", "args": {"path": "x.py", "content": "x"}}
    assert is_approval("yes")
    assert not is_page_query("yes")


# ── State persistence ───────────────────────────────────────────────────────


def test_state_persistence():
    state = AgentState()
    state.current_task = {"goal": "test", "step": "Planning"}
    state.active_project = "immortility"
    state.save()

    AgentState.reset_instance()
    state2 = AgentState()
    assert state2.current_task["goal"] == "test"
    assert state2.active_project == "immortility"


def test_startup_cleanup_clears_stale_pending():
    state = AgentState()
    state.pending_action = {"tool": "create_file", "args": {}}
    state.current_task = {"goal": "old", "step": "Executing", "workflow": "leetcode"}
    state.save()

    state.cleanup_on_startup()
    assert state.pending_action is None
    assert state.current_task is None


# ── Tool registry ───────────────────────────────────────────────────────────


def test_required_tools_registered():
    registry = setup_registry()
    required = {
        "inspect_url",
        "open_url",
        "search_google",
        "scrape_page",
        "get_current_url",
        "get_page_text",
        "create_file",
        "read_file",
        "write_file",
        "list_directory",
        "run_command",
        "open_application",
    }
    registered = set(registry.list_tools())
    assert required.issubset(registered)
