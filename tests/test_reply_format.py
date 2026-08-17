"""Reply polish / format helpers."""

from core.reply_format import polish_reply


def test_polish_strips_bold():
    raw = (
        "The **stocks_app** is a Next.js app.\n\n"
        "- **Real-time** tracking\n"
        "- **TradingView** widgets\n"
    )
    out = polish_reply(raw)
    assert "**" not in out
    assert "stocks_app" in out
    assert "- Real-time tracking" in out
    assert "- TradingView widgets" in out


def test_polish_keeps_paragraph_and_bullets():
    text = "Hello Reyansh, here is the project.\n\n- One\n- Two"
    assert polish_reply(text) == text


def test_polish_strips_headings_fences_and_inline_code():
    raw = "### Title\nThe `urls.py` helper.\n```python\ndef foo():\n    return 1\n```"
    out = polish_reply(raw)
    assert "**" not in out
    assert "###" not in out
    assert "```" not in out
    assert "`" not in out
    assert "urls.py" in out
    assert "def foo():" in out
