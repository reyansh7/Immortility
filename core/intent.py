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
