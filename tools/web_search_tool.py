"""Web search — API providers first, DuckDuckGo Playwright as fallback.

Preferred env cascade (WEB_SEARCH_PROVIDER=auto):
  Tavily → Brave → SearXNG → DuckDuckGo HTML (Playwright)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _max_results() -> int:
    try:
        from core.config import get_config

        return max(1, int(get_config().web_search_max_results))
    except Exception:
        raw = (os.environ.get("WEB_SEARCH_MAX_RESULTS") or "5").strip()
        try:
            return max(1, int(raw))
        except ValueError:
            return 5


def _provider_preference() -> str:
    try:
        from core.config import get_config

        return (get_config().web_search_provider or "auto").strip().lower()
    except Exception:
        return (os.environ.get("WEB_SEARCH_PROVIDER") or "auto").strip().lower()


def resolve_search_provider() -> str:
    """Return concrete provider name that would be used right now."""
    pref = _provider_preference()
    if pref in {"tavily", "brave", "searxng", "ddg", "duckduckgo"}:
        if pref in {"ddg", "duckduckgo"}:
            return "ddg"
        if pref == "tavily" and (os.environ.get("TAVILY_API_KEY") or "").strip():
            return "tavily"
        if pref == "brave" and (os.environ.get("BRAVE_API_KEY") or "").strip():
            return "brave"
        if pref == "searxng" and (os.environ.get("SEARXNG_URL") or "").strip():
            return "searxng"
        return "ddg"
    # auto cascade
    if (os.environ.get("TAVILY_API_KEY") or "").strip():
        return "tavily"
    if (os.environ.get("BRAVE_API_KEY") or "").strip():
        return "brave"
    if (os.environ.get("SEARXNG_URL") or "").strip():
        return "searxng"
    return "ddg"


def _normalize_results(items: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in items:
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or it.get("link") or it.get("href") or "").strip()
        snippet = str(
            it.get("snippet") or it.get("content") or it.get("description") or ""
        ).strip()
        if not url and not title:
            continue
        out.append({"title": title or url, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _search_tavily(query: str, limit: int) -> list[dict[str, str]]:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")
    payload = _http_json(
        "https://api.tavily.com/search",
        method="POST",
        body={
            "api_key": key,
            "query": query,
            "max_results": limit,
            "include_answer": False,
            "search_depth": "basic",
        },
    )
    results = payload.get("results") or []
    mapped = [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("content") or "",
        }
        for r in results
    ]
    return _normalize_results(mapped, limit)


def _search_brave(query: str, limit: int) -> list[dict[str, str]]:
    key = (os.environ.get("BRAVE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY not set")
    qs = urllib.parse.urlencode({"q": query, "count": limit})
    payload = _http_json(
        f"https://api.search.brave.com/res/v1/web/search?{qs}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
    )
    web = (payload.get("web") or {}).get("results") or []
    mapped = [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("description") or "",
        }
        for r in web
    ]
    return _normalize_results(mapped, limit)


def _search_searxng(query: str, limit: int) -> list[dict[str, str]]:
    base = (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL not set")
    qs = urllib.parse.urlencode({"q": query, "format": "json"})
    payload = _http_json(f"{base}/search?{qs}")
    results = payload.get("results") or []
    mapped = [
        {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("content") or "",
        }
        for r in results
    ]
    return _normalize_results(mapped, limit)


async def _search_ddg_playwright(query: str, limit: int) -> list[dict[str, str]]:
    from tools.browser_manager import BrowserManager

    manager = BrowserManager()
    page = await manager.get_page()
    encoded_query = urllib.parse.quote(query)
    await page.goto(f"https://html.duckduckgo.com/html/?q={encoded_query}")
    await page.wait_for_timeout(2000)
    await manager.sync_state()
    results = await page.evaluate(
        """() => {
            const items = [];
            document.querySelectorAll('.result').forEach(el => {
                const titleEl = el.querySelector('.result__title a');
                const snippetEl = el.querySelector('.result__snippet');
                if (titleEl) {
                    let rawUrl = titleEl.getAttribute('href');
                    let actualUrl = rawUrl;
                    if (rawUrl && rawUrl.includes('uddg=')) {
                        try {
                            const params = new URLSearchParams(rawUrl.split('?')[1]);
                            if (params.has('uddg')) {
                                actualUrl = decodeURIComponent(params.get('uddg'));
                            }
                        } catch (e) {}
                    }
                    items.push({
                        title: titleEl.innerText.trim(),
                        url: actualUrl,
                        snippet: snippetEl ? snippetEl.innerText.trim() : ""
                    });
                }
            });
            return items;
        }"""
    )
    return _normalize_results(list(results or []), limit)


class SearchTool:
    """Web search with API providers + DuckDuckGo Playwright fallback."""

    @staticmethod
    async def web_search(query: str) -> dict:
        """Search the live web; returns {status, query, provider, results}."""
        q = (query or "").strip()
        if not q:
            return {"status": "error", "query": query, "provider": "", "results": [], "message": "empty query"}
        limit = _max_results()
        provider = resolve_search_provider()
        results: list[dict[str, str]] = []
        errors: list[str] = []

        order = [provider]
        # Always allow fallback to ddg if API fails
        if provider != "ddg":
            order.append("ddg")

        for name in order:
            try:
                if name == "tavily":
                    results = _search_tavily(q, limit)
                elif name == "brave":
                    results = _search_brave(q, limit)
                elif name == "searxng":
                    results = _search_searxng(q, limit)
                else:
                    results = await _search_ddg_playwright(q, limit)
                    name = "ddg"
                if results:
                    return {
                        "status": "success",
                        "query": q,
                        "provider": name,
                        "results": results,
                    }
                errors.append(f"{name}: empty results")
            except Exception as exc:
                logger.warning("web_search via %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")

        return {
            "status": "error",
            "query": q,
            "provider": provider,
            "results": [],
            "message": "; ".join(errors) or "search failed",
        }

    @staticmethod
    async def search_google(query: str) -> dict:
        """Backward-compatible alias used by ToolRegistry / ResearchAgent."""
        return await SearchTool.web_search(query)
