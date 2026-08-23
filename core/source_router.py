"""Shared source policy: LOCAL | MEMORY | WEB | TOOLS.

Goal: use the web only when facts are time-sensitive or explicitly requested;
prefer memory/RAG when Immortility already has relevant context; stay local
for casual chat and general knowledge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SourceDecision:
    source: str  # LOCAL | MEMORY | WEB | TOOLS
    reason: str
    confidence: float = 0.8


_WEB_EXPLICIT = re.compile(
    r"\b("
    r"research|look\s+up|search\s+the\s+web|find\s+online|google\s+(it|this|for)|"
    r"web\s+search|browse\s+for|check\s+(online|the\s+web)|from\s+the\s+web|"
    r"search\s+online|cite\s+sources|with\s+sources"
    r")\b",
    re.I,
)

# Fresh / changing facts — not general "what is X" knowledge
_WEB_FRESH = re.compile(
    r"\b("
    r"today|tonight|this\s+week|this\s+month|right\s+now|as\s+of\s+now|"
    r"latest|breaking|news|current\s+price|stock\s+price|who\s+won|"
    r"weather|score|election\s+result|"
    r"latest\s+(release|version)|newest\s+version|just\s+released|"
    r"changelog\s+for|what's\s+new\s+in"
    r")\b",
    re.I,
)

_TOOLS = re.compile(
    r"\b(create|edit|delete|write|implement|refactor|fix|run|install|"
    r"open\s+folder|browser_goal|click|fill|scrape|patch|rename)\b",
    re.I,
)

_MEMORY = re.compile(
    r"\b("
    r"remember|you\s+said|you\s+told\s+me|last\s+time|my\s+preference|"
    r"what\s+did\s+we|earlier|previous\s+(fix|error|lesson|answer)|"
    r"from\s+(memory|before)|as\s+we\s+discussed|we\s+already|"
    r"do\s+you\s+recall|your\s+notes|what\s+you\s+learned"
    r")\b",
    re.I,
)

_LOCAL_PROJECT = re.compile(
    r"\b("
    r"my\s+(project|code|repo|file|folder)|this\s+(project|code|file|repo)|"
    r"in\s+(the\s+)?(codebase|repo)|desktop[/\\]?projects?|"
    r"stocks_app|algoverse|skilllens|immortility|"
    r"open\s+project|/open|index\s+(my\s+)?(desktop|project)"
    r")\b",
    re.I,
)

_CASUAL = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank\s+you|ok|okay|cool|great|bye|goodbye)\b",
    re.I,
)

_QUESTION = re.compile(r"\b(what|who|when|where|why|how|is|are|does|did|can)\b", re.I)

_URL = re.compile(r"https?://", re.I)
_THIS_PAGE = re.compile(
    r"\b(this|the|that)\s+(website|site|page|link|url|repo|repository|video|github|youtube)\b",
    re.I,
)


def _context_is_rich(routing_context: str) -> bool:
    ctx = (routing_context or "").strip()
    if len(ctx) < 120:
        return False
    markers = (
        "Learned memory:",
        "PAST EXPERIENCES",
        "PAST MISTAKES",
        "PAST REFLECTIONS",
        ":: ",  # code hit lines from hybrid search
        "[learned:",
    )
    if any(m in ctx for m in markers):
        return True
    # Enough retrieved code/docs text
    return len(ctx) >= 400


def _has_memory_hits(routing_context: str) -> bool:
    ctx = routing_context or ""
    return any(
        m in ctx
        for m in (
            "Learned memory:",
            "PAST EXPERIENCES",
            "PAST MISTAKES",
            "PAST REFLECTIONS",
            "[learned:",
        )
    )


def _lessons_available(query: str) -> bool:
    try:
        from memory.outcome_memory import get_outcome_memory, outcome_learning_enabled

        if not outcome_learning_enabled():
            return False
        return bool(get_outcome_memory().get_relevant_lessons(query, limit=1))
    except Exception:
        return False


def needs_live_web(query: str) -> bool:
    """True for explicit web asks, time-sensitive facts, or a URL to inspect."""
    q = query or ""
    if _URL.search(q):
        return True
    if _THIS_PAGE.search(q):
        try:
            from tools.link_inspect import get_last_url

            if get_last_url():
                return True
        except Exception:
            pass
    if _WEB_EXPLICIT.search(q):
        return True
    if _WEB_FRESH.search(q):
        return True
    return False


def choose_source(
    query: str,
    *,
    routing_context: str = "",
    force: str | None = None,
) -> SourceDecision:
    """Pick LOCAL / MEMORY / WEB / TOOLS.

    Priority:
      1. Forced / empty / casual → LOCAL
      2. Memory recall cues or rich learned context → MEMORY
      3. Local project / codebase asks → LOCAL (or TOOLS if edit verbs)
      4. Explicit web / fresh facts → WEB
      5. Edit/run tools → TOOLS
      6. Default LOCAL (model knowledge + optional RAG already in chat path)
    """
    q = (query or "").strip()
    if force in {"LOCAL", "MEMORY", "WEB", "TOOLS"}:
        return SourceDecision(source=force, reason="forced", confidence=1.0)
    if not q:
        return SourceDecision(source="LOCAL", reason="empty", confidence=1.0)

    if _CASUAL.search(q) and len(q) < 40:
        return SourceDecision(source="LOCAL", reason="casual greeting", confidence=0.95)

    # A concrete URL (or "this website" after we opened/fetched one) is live web.
    if _URL.search(q) or (_THIS_PAGE.search(q) and needs_live_web(q)):
        return SourceDecision(
            source="WEB",
            reason="inspect a URL — fetch the page, do not invent",
            confidence=0.95,
        )

    # Prefer memory when user asks to recall OR we already have strong hits
    if _MEMORY.search(q) or _has_memory_hits(routing_context) or _lessons_available(q):
        # Still allow WEB if they explicitly demand a live lookup
        if _WEB_EXPLICIT.search(q) or _WEB_FRESH.search(q):
            return SourceDecision(
                source="WEB",
                reason="memory available but user asked for live/fresh web facts",
                confidence=0.85,
            )
        return SourceDecision(
            source="MEMORY",
            reason="recall cues or retrieved memory/lessons",
            confidence=0.88,
        )

    local_proj = bool(_LOCAL_PROJECT.search(q))
    tools_hit = bool(_TOOLS.search(q))
    question = bool(_QUESTION.search(q))

    # Project-local work should not dump to the web
    if local_proj:
        if tools_hit and not question:
            return SourceDecision(source="TOOLS", reason="local project action", confidence=0.9)
        if _context_is_rich(routing_context):
            return SourceDecision(
                source="MEMORY",
                reason="local project with retrieved context",
                confidence=0.85,
            )
        return SourceDecision(
            source="LOCAL",
            reason="local project question — use RAG/chat, not web",
            confidence=0.8,
        )

    # Explicit research / time-sensitive
    if _WEB_EXPLICIT.search(q) or _WEB_FRESH.search(q):
        # If we already have dense local docs answering it, prefer MEMORY
        if _context_is_rich(routing_context) and not _WEB_EXPLICIT.search(q):
            # "latest" about something we indexed — still WEB for freshness
            if _WEB_FRESH.search(q):
                return SourceDecision(
                    source="WEB",
                    reason="time-sensitive fact needs live web",
                    confidence=0.9,
                )
        return SourceDecision(
            source="WEB",
            reason="explicit web request or time-sensitive fact",
            confidence=0.92,
        )

    # Mutating / interactive tools
    if tools_hit and not question:
        return SourceDecision(source="TOOLS", reason="action/edit verbs", confidence=0.85)
    if tools_hit and question and re.search(r"\b(how do i|how to)\b", q, re.I):
        # Advisory "how do I fix" stays chat/memory unless they say research
        if _context_is_rich(routing_context):
            return SourceDecision(source="MEMORY", reason="how-to with local context", confidence=0.75)
        return SourceDecision(source="LOCAL", reason="advisory how-to", confidence=0.7)

    if tools_hit:
        return SourceDecision(source="TOOLS", reason="action/edit verbs", confidence=0.75)

    # Rich RAG → answer from memory/context, don't web-search
    if _context_is_rich(routing_context):
        return SourceDecision(
            source="MEMORY",
            reason="sufficient retrieved context",
            confidence=0.8,
        )

    # Default: local model (general knowledge). Do NOT web every "what is".
    return SourceDecision(source="LOCAL", reason="general/local knowledge", confidence=0.65)
