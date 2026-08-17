"""HUD / chat reply shaping — readable plain text, not markdown soup."""

from __future__ import annotations

import re

def reply_format_rules(user_name: str | None = None) -> str:
    """Shared instruction for LLM system prompts (HUD + fast_chat)."""
    name = user_name
    if not name:
        try:
            from memory.user_profile import get_user_name

            name = get_user_name()
        except Exception:
            name = "the user"
    return (
        "Write like a person talking in chat: short sentences, plain text. "
        "Default shape: one short opening paragraph, then a short bullet list "
        "using lines that start with '- ' for the key points. "
        f"If {name} asks for only points / bullets / a list, use bullets with no paragraph. "
        "If they ask for paragraph only / prose only / no bullets, use paragraphs with no list. "
        "Never use markdown: no **bold**, no *italic*, no __underline__, no # headings, "
        "no ``` fences unless they explicitly asked for a code block. "
        "Do not wrap names in asterisks. Do not dump lists of Medium/blog links unless they "
        "explicitly ask for web resources. "
        "When continuing a project they already discussed, give concrete next steps for THAT "
        "project — stay consistent with the earlier plan. "
        "Write clean plain text that looks good in the HUD chat."
    )


# Backward-compatible constant (resolved lazily via profile when possible)
try:
    REPLY_FORMAT_RULES = reply_format_rules()
except Exception:
    REPLY_FORMAT_RULES = reply_format_rules("the user")


_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDER = re.compile(r"__(.+?)__", re.DOTALL)
_ITALIC_STAR = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_HEADING = re.compile(r"(?m)^#{1,6}\s+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_FENCE = re.compile(r"```[\w+-]*\n?")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def polish_reply(text: str) -> str:
    """Strip showy markdown so the HUD does not show raw asterisks."""
    if not text:
        return text
    out = str(text)
    out = _MD_LINK.sub(r"\1 (\2)", out)
    out = _BOLD_STAR.sub(r"\1", out)
    out = _BOLD_UNDER.sub(r"\1", out)
    out = _HEADING.sub("", out)
    out = _ITALIC_STAR.sub(r"\1", out)
    out = _FENCE.sub("", out)
    out = out.replace("```", "")
    out = _INLINE_CODE.sub(r"\1", out)
    out = out.replace("**", "")
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
