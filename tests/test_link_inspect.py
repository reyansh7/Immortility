"""URL inspect: fetch real pages instead of inventing (ECC hallucination fix)."""

from __future__ import annotations

from core.source_router import choose_source, needs_live_web
from tools.link_inspect import (
    extract_urls,
    parse_github_repo,
    parse_youtube_id,
    wants_link_inspect,
)
from tools.user_browser import wants_browser_action


def test_extract_github_url():
    urls = extract_urls(
        "analyze this https://github.com/affaan-m/ECC and tell me about it"
    )
    assert urls == ["https://github.com/affaan-m/ECC"]
    assert parse_github_repo(urls[0]) == ("affaan-m", "ECC")


def test_youtube_id_from_watch_and_short():
    assert parse_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_inspect_intent_beats_chrome_open():
    msg = "i want you to analyze this out and tell me about it https://github.com/affaan-m/ECC"
    assert wants_link_inspect(msg) is True
    assert wants_browser_action(msg) is False


def test_open_url_still_opens_chrome():
    msg = "open https://github.com/affaan-m/ECC"
    assert wants_link_inspect(msg) is False
    assert wants_browser_action(msg) is True


def test_bare_url_is_inspect():
    assert wants_link_inspect("https://github.com/affaan-m/ECC") is True


def test_source_router_url_is_web():
    msg = "tell me about https://github.com/affaan-m/ECC"
    assert needs_live_web(msg) is True
    assert choose_source(msg).source == "WEB"


def test_followup_this_website_uses_last_url(monkeypatch):
    monkeypatch.setattr(
        "tools.link_inspect.get_last_url",
        lambda: "https://github.com/affaan-m/ECC",
    )
    msg = "u just opened it i want you to tell me about this website"
    assert wants_link_inspect(msg) is True
    assert wants_browser_action(msg) is False
    assert choose_source(msg).source == "WEB"


def test_inspect_url_is_readonly():
    from core.pending_action import needs_confirmation

    assert not needs_confirmation("inspect_url", {"url": "https://example.com"})
