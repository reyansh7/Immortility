"""Browser intent parsing: YouTube / Google search from natural language."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from tools.user_browser import extract_browser_targets, wants_browser_action


def _yt_q(url: str) -> str:
    return (parse_qs(urlparse(url).query).get("search_query") or [""])[0]


def test_want_to_see_on_youtube():
    urls = extract_browser_targets("i want to see bb ki vines on youtube")
    assert len(urls) == 1
    assert "youtube.com/results" in urls[0]
    assert _yt_q(urls[0]).lower() == "bb ki vines"


def test_want_to_see_vines_implies_youtube():
    urls = extract_browser_targets("i want to see bb ki vines")
    assert len(urls) == 1
    assert "youtube.com/results" in urls[0]
    assert "bb" in _yt_q(urls[0]).lower()


def test_open_youtube_and_search_with_typo():
    urls = extract_browser_targets("open youtube and searrch bb ki vines")
    assert len(urls) == 1
    assert "youtube.com/results" in urls[0]
    assert _yt_q(urls[0]).lower() == "bb ki vines"


def test_open_query_in_youtube():
    urls = extract_browser_targets("open bb ki vines in youtube")
    assert len(urls) == 1
    assert "youtube.com/results" in urls[0]
    assert _yt_q(urls[0]).lower() == "bb ki vines"


def test_bare_open_youtube_still_home():
    urls = extract_browser_targets("open youtube")
    assert urls == ["https://www.youtube.com"]


def test_search_alone_goes_google():
    urls = extract_browser_targets("search bb ki vines")
    assert len(urls) == 1
    assert "google.com/search" in urls[0]


def test_wants_browser_for_see_intent():
    assert wants_browser_action("i want to see bb ki vines on youtube")
    assert wants_browser_action("open bb ki vines in youtube")


def test_local_code_not_youtube():
    urls = extract_browser_targets("i want to see the stocks_app code")
    assert not any("youtube.com/results" in u for u in urls)
