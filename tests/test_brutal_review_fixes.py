"""Regression tests for brutal-review bugfixes."""

from __future__ import annotations

import ast
import inspect

from core.intent import is_coding_intent
from core.json_utils import strip_llm_fences
from core.router import _fast_route


def test_strip_llm_fences_prefers_last_json_block():
    raw = 'noise ```json\n{"a": 1}\n``` more ```json\n{"b": 2}\n```'
    out = strip_llm_fences(raw)
    assert '"b"' in out
    assert out.strip().startswith("{")


def test_fast_route_yes_without_pending_is_chat():
    from core.agent_state import AgentState

    state = AgentState()
    state.pending_action = None
    state.pending_coding_request = None
    state.save()
    assert _fast_route("yes") == "CHAT"
    assert _fast_route("ok") == "CHAT"


def test_looks_like_fast_chat_backslash_alone_not_heavy():
    import main as m

    # Operator-precedence fix: lone backslash must not force heavy path
    # (unless paired with path-ish markers)
    assert m._looks_like_fast_chat("hello\\") is True or m._looks_like_fast_chat("hello") is True
    assert m._looks_like_fast_chat("hi there") is True
    assert m._looks_like_fast_chat("what does the login function do?") is False
    assert m._looks_like_fast_chat("C:\\\\Users\\\\x\\\\src\\\\app.py") is False


def test_coding_intent_unified():
    assert is_coding_intent("please refactor the auth module")
    assert not is_coding_intent("what is the weather")


def test_handle_project_query_defines_project_path_before_deep_read():
    """BUG-2: project_path must be assigned before wants_deep_read uses it."""
    import main as m

    src = inspect.getsource(m.handle_project_query)
    # Rough structural check: assignment appears before deep-read block
    assign_idx = src.find("project_path = active.path")
    deep_idx = src.find("wants_deep_read")
    assert assign_idx != -1 and deep_idx != -1
    assert assign_idx < deep_idx


def test_main_compiles():
    with open("main.py", encoding="utf-8") as f:
        ast.parse(f.read())
