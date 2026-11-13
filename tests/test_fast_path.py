"""FAST HUD path must not enter the Action Engine."""

import os
import subprocess
import sys
from pathlib import Path

from tools import hud_agent
from core.intent import instant_reply


def test_greeting_does_not_touch_model_or_index(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("greeting must not call the model or knowledge engine")

    class _QuietState:
        pending_action = None

    monkeypatch.setattr("core.llm.fast_chat", boom)
    monkeypatch.setattr("core.agent_state.AgentState", lambda: _QuietState())
    assert instant_reply("hi") == "Hey. I'm here."
    reply = hud_agent.handle_hud_request("hi")
    assert reply == "Hey. I'm here."
    assert hud_agent.handle_hud_request("Hello!") == "Hey. I'm here."


def test_fast_question_skips_action_engine(monkeypatch):
    called = {"action": 0}

    async def fake_execute(*args, **kwargs):
        called["action"] += 1
        return "ACTION_RAN"

    monkeypatch.setattr("core.action_engine.execute_action", fake_execute)
    monkeypatch.setattr("core.llm.fast_chat", lambda *a, **k: "four")
    monkeypatch.setattr(
        "core.capabilities.wants_capability_report", lambda m: False
    )
    reply = hud_agent.handle_hud_request("what is 2+2")
    assert called["action"] == 0
    assert "ACTION_RAN" not in (reply or "")
    assert "four" in (reply or "")


def test_tracing_import_does_not_load_harness_engine():
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "from core.harness.tracing import begin_turn\n"
        "assert 'core.harness.engine' not in sys.modules\n"
        "begin_turn(mode='FAST')\n"
        "assert 'core.harness.engine' not in sys.modules\n"
        "from core.harness import begin_turn as pkg_begin\n"
        "pkg_begin(mode='FAST')\n"
        "assert 'core.harness.engine' not in sys.modules\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tool_registry_import_does_not_load_computer_use():
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import tools.tool_registry\n"
        "assert 'tools.computer_tool' not in sys.modules\n"
        "assert 'tools.browser_tool' not in sys.modules\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fast_question_skips_desktop_scanner(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("FAST chat must not scan the desktop")

    monkeypatch.setattr("core.llm.fast_chat", lambda *a, **k: "ok")
    monkeypatch.setattr("core.capabilities.wants_capability_report", lambda m: False)
    monkeypatch.setattr("tools.hud_agent._try_scan_single_folder", boom)
    reply = hud_agent.handle_hud_request("what is gravity")
    assert "ok" in (reply or "")
