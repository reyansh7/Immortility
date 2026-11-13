"""Unified intent keyword sets — single source for routing / coding detection."""

from __future__ import annotations

# Coding / project-edit intents (shared by router, action_engine, main)
CODING_KEYWORDS: tuple[str, ...] = (
    "implement",
    "fix ",
    "refactor",
    "rename",
    "debug",
    "error",
    "create file",
    "create component",
    "build project",
    "make the changes",
    "make these changes",
    "edit file",
    "modify file",
    "update file",
    "write code",
    "add ",
    "convert",
)

# Heavy chat → skip fast_chat short-circuit
HEAVY_CHAT_HINTS: tuple[str, ...] = (
    "fix",
    "bug",
    "error",
    "implement",
    "refactor",
    "edit",
    "create file",
    "write code",
    "patch",
    "commit",
    "pull request",
    "debug",
    "traceback",
    "open project",
    "run the",
    "install",
    "deploy",
    "test suite",
    "leetcode",
    "find those changes",
    "find out those changes",
    "what changed",
    "git status",
    "git diff",
)

STOP_PHRASES: frozenset[str] = frozenset({
    "stop",
    "exit",
    "quit",
    "goodbye",
    "good bye",
    "bye",
    "end talk",
    "stop talking",
    "end conversation",
})


def is_coding_intent(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in CODING_KEYWORDS)


def is_heavy_chat(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in HEAVY_CHAT_HINTS)


_SMALLTALK = frozenset({
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "cool",
    "good morning",
    "good night",
    "good evening",
    "how are you",
    "how's it going",
    "hows it going",
    "who are you",
})


def instant_reply(text: str) -> str | None:
    """Local reply for greetings. No model, no retrieval, no disk index."""
    import re

    key = re.sub(r"[^a-z0-9' ]+", "", (text or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if key not in _SMALLTALK:
        return None
    if key in {"thanks", "thank you"}:
        return "Anytime."
    if key in {"how are you", "how's it going", "hows it going"}:
        return "Ready. What do you want to do?"
    if key == "who are you":
        return "I'm Immortility, running on your machine. Tell me what you want done."
    if key in {"ok", "okay", "cool"}:
        return "Okay."
    if key == "good morning":
        return "Good morning."
    if key == "good night":
        return "Good night."
    if key == "good evening":
        return "Good evening."
    return "Hey. I'm here."
