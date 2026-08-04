"""Chat thread continuity + advisory routing."""

from __future__ import annotations

from core.chat_thread import (
    extract_topic_title,
    focus_context_for_message,
    is_advisory_chat,
)
from core.reply_format import polish_reply
from tools.hud_agent import _wants_action


def test_advisory_how_to_start():
    assert is_advisory_chat("how do i start the network traffic project")
    assert is_advisory_chat("help me out in this")
    assert not is_advisory_chat("create a folder named traffic_detector on Desktop")
    assert not _wants_action("how do i start the network traffic project")


def test_extract_network_title():
    title = extract_topic_title(
        "Network Traffic Anomaly Detector (Networks + Statistical Methods + Python for Data Science)"
    )
    assert title is not None
    assert "Network Traffic" in title


def test_focus_context_uses_saved_topic(monkeypatch):
    from core import agent_state as ag

    ag.AgentState.reset_instance()
    st = ag.AgentState()
    st.chat_focus = {
        "title": "Network Traffic Anomaly Detector",
        "summary": "Key Steps:\n- Capture with Wireshark\n- Z-score anomalies",
        "kind": "project_idea",
    }
    ctx = focus_context_for_message("how do i start the network traffic project")
    assert "Network Traffic Anomaly Detector" in ctx
    assert "Wireshark" in ctx
    assert "Medium" in ctx or "do NOT" in ctx


def test_polish_strips_headings_and_links():
    raw = (
        "### How to Start\n\n"
        "1. **[Title - Medium](https://medium.com/x)**\n"
        "   - Description: foo\n"
    )
    out = polish_reply(raw)
    assert "**" not in out
    assert "###" not in out
    assert "Title - Medium" in out
    assert "medium.com" in out
