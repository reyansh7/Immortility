"""FAST / AGENT / BACKGROUND — execution strategies, not capability categories.

Classification uses intent, complexity, modality, required capabilities, and
latency — never a table of product names (PDF, resume, browser, …).
"""

from __future__ import annotations

import re

from core.harness import MODE_AGENT, MODE_BACKGROUND, MODE_FAST
from core.intent import is_coding_intent
from core.project_extract import is_run_project_request

VALID_STRATEGIES = (MODE_FAST, MODE_AGENT, MODE_BACKGROUND)

_BACKGROUND_HINTS = (
    r"\bin the background\b",
    r"\bbackground (this|task|job|it)\b",
    r"\bindex (my )?(desktop )?projects\b",
    r"\breindex\b",
    r"\bingest\b",
    r"\bfull audit\b",
    r"\bdeep (dive|research)\b",
    r"\bthousands of pages\b",
    r"\b\d{3,}\s*pages?\b",
    r"\blarge (pdf|document|corpus|dataset)\b",
    r"\bscan (all|the whole|my entire)\b",
)

_AGENT_HINTS = (
    r"\b(debug|fix|implement|refactor|edit|patch|commit|push)\b",
    r"\b(run|execute|install|build|test)\b",
    r"\b(open|launch|create|delete|write|kill)\b",
    r"https?://",
    r"\binspect (this )?(url|link|repo|page)\b",
    r"\bsearch (the )?(web|internet|google)\b",
    r"\b(look (at|through) (this|the) (code|repo|project|file))\b",
    r"\b(git (status|diff|log)|uncommitted|working tree)\b",
    r"\b(what(?:'s|s)? changed|made changes|find (out )?(those |the )?changes)\b",
)

_FAST_HINTS = (
    r"^\s*(hi|hello|hey|thanks|thank you)\s*[!.]?\s*$",
    r"\bwhat (model|llm) are you\b",
    r"\bwho are you\b",
    r"\btell me about yourself\b",
    r"\bwhat can you do\b",
    r"\bwhat you can do\b",
    r"\byour capabilities\b",
    r"\bwhat is \d+\s*[+\-*/]\s*\d+",
    r"^\s*what is\b.{0,80}$",
    r"^\s*explain .{0,60} in (one|a) sentence",
)


def classify_strategy(user_input: str, *, category: str | None = None) -> str:
    """Pick FAST | AGENT | BACKGROUND from the request, not from a task catalog."""
    text = (user_input or "").strip()
    if not text:
        return MODE_FAST
    low = text.lower()

    if any(re.search(pat, low) for pat in _BACKGROUND_HINTS):
        return MODE_BACKGROUND
    if is_run_project_request(text):
        return MODE_AGENT
    if is_coding_intent(text):
        return MODE_AGENT
    if any(re.search(pat, low) for pat in _AGENT_HINTS):
        return MODE_AGENT
    if any(re.search(pat, low) for pat in _FAST_HINTS):
        return MODE_FAST

    cat = (category or "").upper()
    if cat in {"ACTION", "PROJECT"}:
        return MODE_AGENT
    if cat in {"TASK", "RESEARCH_TASK"}:
        if any(w in low for w in ("comprehensive", "everything", "all sources", "entire")):
            return MODE_BACKGROUND
        return MODE_AGENT
    if cat == "CHAT":
        return MODE_FAST

    # Unseen phrasing: length + tool-ish verbs vs short Q&A.
    if len(text) > 400:
        return MODE_BACKGROUND
    if len(text.split()) <= 22 and not re.search(
        r"\b(file|repo|project|code|url|browser|terminal|git)\b", low
    ):
        return MODE_FAST
    return MODE_AGENT
