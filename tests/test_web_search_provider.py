"""Phase 2A — web search provider selection (no live network required)."""

from __future__ import annotations

import os

from tools.web_search_tool import (
    _normalize_results,
    resolve_search_provider,
)


def test_normalize_results_shape():
    raw = [
        {"title": "A", "url": "https://a.example", "content": "snippet a"},
        {"title": "B", "link": "https://b.example", "description": "snippet b"},
    ]
    out = _normalize_results(raw, limit=5)
    assert len(out) == 2
    assert out[0]["url"] == "https://a.example"
    assert out[0]["snippet"] == "snippet a"
    assert set(out[0].keys()) == {"title", "url", "snippet"}


def test_resolve_provider_auto_prefers_tavily(monkeypatch=None):
    # Compatible with plain unittest-style run without pytest fixtures
    old = {k: os.environ.get(k) for k in (
        "WEB_SEARCH_PROVIDER", "TAVILY_API_KEY", "BRAVE_API_KEY", "SEARXNG_URL"
    )}
    try:
        os.environ["WEB_SEARCH_PROVIDER"] = "auto"
        os.environ["TAVILY_API_KEY"] = "tvly-test"
        os.environ.pop("BRAVE_API_KEY", None)
        os.environ.pop("SEARXNG_URL", None)
        # Clear config cache so env is re-read
        try:
            from core.config import reset_config_cache

            reset_config_cache()
        except Exception:
            pass
        assert resolve_search_provider() == "tavily"

        os.environ.pop("TAVILY_API_KEY", None)
        os.environ["BRAVE_API_KEY"] = "brave-test"
        try:
            from core.config import reset_config_cache

            reset_config_cache()
        except Exception:
            pass
        assert resolve_search_provider() == "brave"

        os.environ.pop("BRAVE_API_KEY", None)
        os.environ["SEARXNG_URL"] = "http://localhost:8080"
        try:
            from core.config import reset_config_cache

            reset_config_cache()
        except Exception:
            pass
        assert resolve_search_provider() == "searxng"

        os.environ.pop("SEARXNG_URL", None)
        try:
            from core.config import reset_config_cache

            reset_config_cache()
        except Exception:
            pass
        assert resolve_search_provider() == "ddg"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            from core.config import reset_config_cache

            reset_config_cache()
        except Exception:
            pass
