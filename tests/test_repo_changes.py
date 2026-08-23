"""Repo-change questions must use git primitives, not RAG/folder-map hallucination."""

from __future__ import annotations

from tools.git_tool import (
    format_repo_change_evidence,
    wants_repo_changes,
)
from tools.self_inspect import wants_self_inspect
from core.execution_mode import MODE_AGENT, classify_strategy


USER_QUERY = "i made changes in your code base find out those changes"


def test_user_change_query_is_git_not_self_inspect():
    assert wants_repo_changes(USER_QUERY)
    assert wants_repo_changes("what changed in this repo")
    assert wants_repo_changes("git diff")
    assert not wants_repo_changes("make the changes")
    assert not wants_repo_changes("hi how are you")
    assert not wants_self_inspect(USER_QUERY)


def test_change_query_is_agent_strategy():
    assert classify_strategy(USER_QUERY) == MODE_AGENT


def test_evidence_lists_only_git_paths():
    bundle = {
        "status": {
            "status": "success",
            "repo": "C:/immortility1",
            "branch": "main",
            "clean": False,
            "files": [{"xy": " M", "path": "tools/git_tool.py"}],
        },
        "diff": {
            "status": "success",
            "files": ["a/tools/git_tool.py"],
            "patch": "diff --git a/tools/git_tool.py b/tools/git_tool.py\n+hello",
        },
        "staged": {"status": "success", "files": [], "patch": ""},
        "log": {
            "status": "success",
            "commits": [{"sha": "abc1234567", "subject": "feat: 2b tools"}],
        },
    }
    text = format_repo_change_evidence(bundle)
    assert "tools/git_tool.py" in text
    assert "scripts/run_agents.py" not in text
    assert "docs/architecture.md" not in text
    assert "knowledge/init.py" not in text


def test_hud_uses_git_report_not_action_or_self_inspect(monkeypatch):
    called = {"git": 0, "action": 0, "self": 0}

    def fake_git(message, cwd=None):
        called["git"] += 1
        return "REAL_GIT: tools/git_tool.py"

    async def fake_action(*args, **kwargs):
        called["action"] += 1
        return "ACTION_RAN"

    monkeypatch.setattr("tools.git_tool.answer_repo_changes", fake_git)
    monkeypatch.setattr("core.action_engine.execute_action", fake_action)
    monkeypatch.setattr(
        "tools.self_inspect.handle_self_inspect",
        lambda m: called.__setitem__("self", called["self"] + 1) or "SELF",
    )
    monkeypatch.setattr(
        "core.desktop_scanner.wants_projects_scan", lambda m: False
    )

    from core.agent_state import AgentState

    AgentState.reset_instance()
    from tools import hud_agent

    reply = hud_agent.handle_hud_request(USER_QUERY)
    assert called["git"] == 1
    assert called["action"] == 0
    assert called["self"] == 0
    assert "REAL_GIT" in (reply or "")
    assert "ACTION_RAN" not in (reply or "")
    AgentState.reset_instance()
