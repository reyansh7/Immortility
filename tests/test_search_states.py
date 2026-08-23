"""Web search state machine."""

from tools.web_search_tool import (
    SEARCH_EXECUTED_ZERO_RESULTS,
    SEARCH_SUCCESS,
    SEARCH_UNAVAILABLE,
    SearchTool,
)


def test_empty_query_is_unavailable_shape():
    import asyncio

    result = asyncio.run(SearchTool.web_search("   "))
    assert result["status"] == "error"
    assert "search_state" not in result or result.get("results") == []


def test_zero_results_state(monkeypatch):
    import asyncio

    async def fake_ddg(query, limit):
        return []

    monkeypatch.setattr(
        "tools.web_search_tool.resolve_search_provider", lambda: "ddg"
    )
    monkeypatch.setattr("tools.web_search_tool._search_ddg_playwright", fake_ddg)
    result = asyncio.run(SearchTool.web_search("unlikely-query-xyz"))
    assert result["search_state"] == SEARCH_EXECUTED_ZERO_RESULTS
    assert result["results"] == []
    assert "nothing" in (result.get("message") or "").lower() or result["search_state"] == SEARCH_EXECUTED_ZERO_RESULTS


def test_unavailable_when_providers_throw(monkeypatch):
    import asyncio

    async def boom(query, limit):
        raise RuntimeError("offline")

    monkeypatch.setattr(
        "tools.web_search_tool.resolve_search_provider", lambda: "ddg"
    )
    monkeypatch.setattr("tools.web_search_tool._search_ddg_playwright", boom)
    result = asyncio.run(SearchTool.web_search("anything"))
    assert result["search_state"] == SEARCH_UNAVAILABLE
    assert "unavailable" in (result.get("message") or "").lower() or result["status"] == "error"


def test_success_state(monkeypatch):
    import asyncio

    async def hits(query, limit):
        return [{"title": "Example", "url": "https://example.com", "snippet": "hi"}]

    monkeypatch.setattr(
        "tools.web_search_tool.resolve_search_provider", lambda: "ddg"
    )
    monkeypatch.setattr("tools.web_search_tool._search_ddg_playwright", hits)
    result = asyncio.run(SearchTool.web_search("example"))
    assert result["search_state"] == SEARCH_SUCCESS
    assert result["results"][0]["url"] == "https://example.com"
