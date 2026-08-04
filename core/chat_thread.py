"""HUD chat thread continuity — remember what Reyansh is talking about."""

from __future__ import annotations

import re
from typing import Any


_ADVISORY = re.compile(
    r"\b("
    r"how\s+(?:do\s+i|to|can\s+i|should\s+i)|"
    r"help\s+me(?:\s+out)?|"
    r"guide\s+me|"
    r"walk\s+me\s+through|"
    r"what\s+(?:should\s+i|next|now)|"
    r"explain\s+how|"
    r"get\s+started|"
    r"getting\s+started|"
    r"next\s+steps?"
    r")\b",
    re.I,
)

_HARD_ACTION = re.compile(
    r"\b("
    r"create\s+(?:a\s+|the\s+)?(?:file|folder|repo|directory)|"
    r"write\s+(?:the\s+)?code|"
    r"implement\s+it\s+now|"
    r"run\s+(?:npm|python|uvicorn|the\s+server)|"
    r"install\s+\w+|"
    r"open\s+(?:the\s+)?folder|"
    r"delete\s+(?:the\s+)?file"
    r")\b",
    re.I,
)

_REFERS_BACK = re.compile(
    r"\b("
    r"this|that|it|the\s+project|this\s+project|that\s+project|"
    r"the\s+idea|this\s+idea|same\s+one|above|earlier|"
    r"start(?:ing)?(?:\s+it)?|continue|next\s+step"
    r")\b",
    re.I,
)

_IDEA_TITLE = re.compile(
    r"(?i)(?:project\s+idea\s*[:\-–]\s*)?([A-Z][A-Za-z0-9][A-Za-z0-9 +\-/&()]{6,80})"
)


def is_advisory_chat(message: str) -> bool:
    """True for guidance questions that must stay in chat (not action/web-search)."""
    low = (message or "").strip()
    if not low:
        return False
    if not _ADVISORY.search(low):
        return False
    if _HARD_ACTION.search(low):
        return False
    return True


def _clip(text: str, n: int = 900) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def extract_topic_title(text: str) -> str | None:
    """Pull a likely project-idea title from a user/assistant message."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Explicit "Project Idea: …"
    m = re.search(r"(?i)project\s+idea\s*[:\-–]\s*(.+)", raw)
    if m:
        title = m.group(1).split("\n")[0].strip(" \t-*#")
        if 4 <= len(title) <= 100:
            return title
    # First line looks like a titled idea (user pasted a name)
    first = raw.split("\n")[0].strip()
    if (
        8 <= len(first) <= 100
        and not first.lower().startswith(("hi", "hello", "hey", "thanks"))
        and re.search(
            r"(?i)\b(detector|analyzer|dashboard|generator|system|app|tool|tracker|monitor)\b",
            first,
        )
    ):
        return first
    return None


def update_focus_after_turn(
    user_message: str,
    assistant_reply: str,
    history: list[dict[str, str]] | None = None,
) -> None:
    """Persist the active chat topic so later 'how do I start it' stays on track."""
    try:
        from core.agent_state import AgentState

        state = AgentState()
        title = extract_topic_title(user_message) or extract_topic_title(assistant_reply)
        summary_bits: list[str] = []

        if title:
            summary_bits.append(f"Topic: {title}")
        # Keep a compact slice of the assistant plan if it looks like guidance
        ar = (assistant_reply or "").strip()
        if ar and (
            re.search(r"(?i)\b(key\s+steps|overview|tech\s+stack|features)\b", ar)
            or title
        ):
            summary_bits.append(_clip(ar, 1200))
        elif history:
            # Fall back: last substantial assistant message
            for m in reversed(history[-8:]):
                if (m.get("role") or "") in {"assistant", "model"}:
                    c = (m.get("content") or "").strip()
                    if len(c) > 80:
                        summary_bits.append(_clip(c, 900))
                        if not title:
                            title = extract_topic_title(c)
                        break

        if not title and not summary_bits:
            return

        # Merge with prior focus if this turn is a follow-up without a new title
        prior = state.chat_focus if isinstance(state.chat_focus, dict) else {}
        if not title and prior.get("title"):
            title = str(prior.get("title"))
        state.chat_focus = {
            "title": title or prior.get("title") or "current topic",
            "summary": "\n\n".join(summary_bits)[:2000]
            or str(prior.get("summary") or ""),
            "kind": "project_idea"
            if title or "project" in (user_message or "").lower()
            else "topic",
        }
        state.save()
    except Exception:
        return


def focus_context_for_message(message: str) -> str:
    """If the user is referring to the ongoing thread, return focus text for the LLM."""
    try:
        from core.agent_state import AgentState

        focus = AgentState().chat_focus
    except Exception:
        return ""
    if not isinstance(focus, dict) or not (focus.get("title") or focus.get("summary")):
        return ""

    title = str(focus.get("title") or "")
    summary = str(focus.get("summary") or "")
    low = (message or "").lower()
    title_tokens = [t for t in re.split(r"[^a-z0-9]+", title.lower()) if len(t) > 3]
    overlap = sum(1 for t in title_tokens if t in low)
    refers = bool(_REFERS_BACK.search(message or "")) or overlap >= 2 or is_advisory_chat(
        message or ""
    )
    if not refers and title and title.lower()[:20] not in low:
        # Still inject softly when advisory about "network traffic" etc.
        if not any(t in low for t in title_tokens[:4]):
            return ""

    return (
        f"Ongoing chat topic: {title}\n"
        f"What you already told Reyansh about it (stay consistent, continue from here, "
        f"do NOT dump random Medium/blog search results):\n{_clip(summary, 1400)}"
    )


def history_for_model(
    history: list[dict[str, str]] | None,
    *,
    max_turns: int = 16,
) -> list[dict[str, str]]:
    """Trim history but keep enough turns for follow-ups; clip huge assistant blobs."""
    if not history:
        return []
    out: list[dict[str, str]] = []
    for m in history[-max_turns:]:
        role = (m.get("role") or "user").lower()
        if role == "model":
            role = "assistant"
        content = (m.get("content") or "").strip()
        if not content or role not in {"user", "assistant"}:
            continue
        if role == "assistant" and len(content) > 1200:
            content = _clip(content, 1200)
        out.append({"role": role, "content": content})
    return out
