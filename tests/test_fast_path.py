"""FAST HUD path must not enter the Action Engine."""

from tools import hud_agent


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
