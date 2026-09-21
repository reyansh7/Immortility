"""Action engine and tool registry behavior tests."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.action_engine import _tool_call_failed, execute_action
from core.agent_state import AgentState
from tools.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def reset_state(tmp_path, monkeypatch):
    AgentState.reset_instance()
    ToolRegistry.reset_instance()
    monkeypatch.setenv("IMMORTILITY_AGENT_BACKEND", "legacy")
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    from core.config import reset_config_cache
    reset_config_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.txt").write_text("test", encoding="utf-8")
    yield
    reset_config_cache()
    AgentState.reset_instance()
    ToolRegistry.reset_instance()


def test_tool_call_failed_detects_nested_error():
    payload = json.dumps({
        "status": "success",
        "tool": "edit_file",
        "result": {"status": "error", "message": "Patch would introduce a syntax error"},
    })
    assert _tool_call_failed(payload)


def test_tool_call_failed_detects_top_level_error():
    payload = json.dumps({"status": "error", "tool": "edit_file", "message": "bad args"})
    assert _tool_call_failed(payload)


def test_registry_propagates_inner_edit_error(tmp_path):
    f = tmp_path / "page.tsx"
    f.write_text("export default function X() { return <p>Don't</p>; }\n", encoding="utf-8")

    registry = ToolRegistry()
    registry.setup()

    import asyncio

    result = asyncio.run(
        registry.execute("edit_file", {
            "path": str(f),
            "target_text": "export default function X()",
            "replacement_text": "export default function X(",  # breaks braces
        })
    )
    data = json.loads(result)
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_action_engine_stops_after_repeated_failures(tmp_path):
    f = tmp_path / "page.tsx"
    f.write_text("export default function X() { return <div>ok</div>; }\n", encoding="utf-8")

    read_json = json.dumps({
        "tool": "read_file", "reason": "read", "confidence": 1.0,
        "args": {"path": str(f)},
    })
    fail_edit = json.dumps({
        "tool": "edit_file", "reason": "edit", "confidence": 0.9,
        "args": {"path": str(f), "target_text": "ok", "replacement_text": "ok("},
    })
    responses = [{"message": {"content": read_json}}] + [
        {"message": {"content": fail_edit}} for _ in range(6)
    ]

    # The legacy loop uses the execution-kernel model boundary; patch that
    # boundary so this remains a deterministic unit test without a live LLM.
    with patch("core.action_engine._kernel_chat") as mock_chat:
        mock_chat.side_effect = responses
        # auto_confirm bypasses the mutator confirmation gate so the loop can
        # actually reach the repeated-failure guard under test.
        msg = await execute_action("fix page", require_edits=True, auto_confirm=True)

    assert mock_chat.call_count <= 5
    lower = msg.lower()
    assert (
        "completion" in lower
        or "stopping" in lower
        or "failed identically twice" in lower
        or "halting" in lower
    )
