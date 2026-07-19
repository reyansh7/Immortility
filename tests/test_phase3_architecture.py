"""Phase 3: Smart Context Builder, CI completion gate, Browser Planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from editing.ci_gate import CIGate
from knowledge.hierarchical_memory import HierarchicalMemory
from knowledge.knowledge_graph_db import KnowledgeGraphDB
from knowledge.smart_context_builder import SmartContextBuilder
from rag.retriever import RetrievalResult
from tools.browser_agent import BrowserAgent


def _write_multi_file_project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "services").mkdir(parents=True)
    (root / "utils").mkdir()
    # Dependent file is intentionally large so we can assert it is NOT dumped
    big_helper = "\n".join(f"    # helper line {i}" for i in range(200))
    (root / "utils" / "tokens.py").write_text(
        f"""
def mint_token(user: str) -> str:
{big_helper}
    return f"tok-{{user}}"

def verify_token(token: str) -> bool:
    return token.startswith("tok-")
""".lstrip(),
        encoding="utf-8",
    )
    (root / "services" / "auth.py").write_text(
        """
from utils.tokens import mint_token

def login(user: str) -> str:
    return mint_token(user)
""".lstrip(),
        encoding="utf-8",
    )
    return root


def test_smart_context_includes_graph_imports_not_whole_dependent_file(tmp_path):
    root = _write_multi_file_project(tmp_path)
    db = KnowledgeGraphDB(tmp_path / "kg.db")
    mem = HierarchicalMemory(db=db)
    mem.ingest_project(root, project="app")
    db.set_fact("app", "framework", "fastapi")
    db.upsert_repo_summary("app", "Demo auth service with token utilities.")

    hits = mem.lookup_symbol("login", project="app")
    assert hits

    builder = SmartContextBuilder(max_chars=12_000)
    # Fake semantic chunk only from auth.py — not the huge tokens.py body
    chunks = [
        RetrievalResult(
            content="def login(user: str) -> str:\n    return mint_token(user)\n",
            filename="services/auth.py",
            relevance_score=0.9,
            chunk_type="function",
            function_name="login",
        )
    ]
    prompt = builder.build(
        user_task="Update login in auth.py to validate mint_token output",
        project_facts=db.list_facts("app"),
        repo_summary=db.get_repo_summary("app"),
        graph_hits=hits,
        semantic_chunks=chunks,
        current_file_state="",
        recent_diff="",
    )

    # Strict section order / headers
    for title in SmartContextBuilder.SECTION_ORDER:
        assert f"## [{title}]" in prompt

    assert "fastapi" in prompt
    assert "login" in prompt
    assert "mint_token" in prompt or "tokens" in prompt.lower()
    # Must NOT embed the giant dependent file body
    assert "helper line 50" not in prompt
    assert "helper line 199" not in prompt
    # Semantic chunks capped: only auth snippet present as chunk body
    assert prompt.count("### chunk") <= 4


def test_smart_context_caps_semantic_chunks_at_four():
    builder = SmartContextBuilder()
    chunks = [
        RetrievalResult(
            content=f"code {i}",
            filename=f"f{i}.py",
            relevance_score=1.0 - i * 0.01,
            chunk_type="code",
        )
        for i in range(10)
    ]
    prompt = builder.build(user_task="do something", semantic_chunks=chunks)
    assert prompt.count("### chunk") == 4


def test_ci_gate_stage_order_and_lint_failure(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def oops(\n", encoding="utf-8")  # syntax error
    gate = CIGate(tmp_path)
    result = gate.run_full_pipeline([str(bad)])
    assert result["success"] is False
    assert result["stage"] == "lint"
    assert result["stages"]
    assert result["stages"][0]["name"] == "lint"


def test_ci_gate_targeted_passes_valid_file(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    gate = CIGate(tmp_path)
    result = gate.run_targeted([str(ok)])
    assert result["success"] is True
    assert result["stage"] == "targeted"


def test_ci_gate_full_pipeline_success_mocked(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")
    gate = CIGate(tmp_path)
    with patch.object(gate, "_typecheck", return_value={"ok": True, "name": "typecheck", "detail": "ok"}):
        with patch.object(gate, "_full_tests", return_value={"ok": True, "name": "tests", "detail": "ok"}):
            result = gate.run_full_pipeline([str(ok)])
    assert result["success"] is True
    assert result["stage"] == "complete"
    names = [s["name"] for s in result["stages"]]
    assert names == ["lint", "typecheck", "tests"]


def test_browser_plan_uses_roles_not_coordinates():
    plan = BrowserAgent.plan(
        'Search YouTube for "lofi hip hop" and click on "Filters"',
        start_url="https://www.youtube.com",
    )
    assert plan["actions"]
    ops = {a["op"] for a in plan["actions"]}
    assert "goto" in ops
    assert "fill_role" in ops or "click_role" in ops
    for a in plan["actions"]:
        assert "x" not in a and "y" not in a
        assert "coordinate" not in a
        if a["op"] in ("fill_role", "click_role"):
            assert "role" in a


@pytest.mark.asyncio
async def test_browser_validate_rejects_failed_execute():
    action = {"op": "goto", "url": "https://example.com", "expect": {"url_contains": "example"}}
    val = await BrowserAgent.validate(action, {"ok": False, "error": "timeout"})
    assert val["ok"] is False
    assert "timeout" in val["reason"]


@pytest.mark.asyncio
async def test_browser_validate_url_contains_ok(monkeypatch):
    class FakePage:
        url = "https://www.example.com/page"

    class FakeManager:
        async def get_page(self):
            return FakePage()

    monkeypatch.setattr("tools.browser_agent.BrowserManager", FakeManager)
    action = {"op": "goto", "url": "https://example.com", "expect": {"url_contains": "example.com"}}
    val = await BrowserAgent.validate(action, {"ok": True, "before_url": "about:blank"})
    assert val["ok"] is True


def test_browser_goal_registered_in_tool_registry():
    from tools.tool_registry import ToolRegistry

    # Fresh registry instance may already be singleton — force setup
    reg = ToolRegistry()
    reg._setup_done = False
    reg._tools = {}
    reg.setup()
    assert "browser_goal" in reg.list_tools()
