"""Persistent conversation memory — tracks completed work, bug fixes, and recent actions.

Data is stored as JSON in ``memory/data/conversations.json``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_conversations_path() -> Path:
    try:
        from core.repo_paths import conversations_path

        return conversations_path()
    except Exception:
        return Path("memory/data/conversations.json")


def _max_entries() -> int:
    try:
        from core.config import get_config

        return get_config().max_conversation_entries
    except Exception:
        return 200


CONVERSATIONS_FILE = str(_default_conversations_path())
MAX_ENTRIES = 200  # fallback; prefer _max_entries() at runtime


@dataclass
class ConversationEntry:
    """A single remembered conversation event."""

    event_type: str  # "task" | "bug_fix" | "action" | "note"
    summary: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationMemory:
    """Stores and retrieves conversation history summaries.

    Tracks:
    - Completed tasks
    - Bug fixes
    - Recent actions
    - General notes
    """

    def __init__(self, filepath: str | Path | None = None) -> None:
        self._path = Path(filepath) if filepath else _default_conversations_path()
        self._entries: list[ConversationEntry] = []
        self._load()

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._entries = [ConversationEntry(**e) for e in raw]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Corrupt conversation memory, starting fresh: %s", exc)
            self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Trim to max entries
        limit = _max_entries()
        if len(self._entries) > limit:
            self._entries = self._entries[-limit:]
        raw = [asdict(e) for e in self._entries]
        self._path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    # ── Public API ──────────────────────────────────────────────────

    def add_task(self, summary: str, metadata: dict[str, Any] | None = None) -> None:
        """Record a completed task."""
        self._entries.append(
            ConversationEntry(
                event_type="task",
                summary=summary,
                metadata=metadata or {},
            )
        )
        self._save()
        logger.debug("Conversation memory: recorded task '%s'", summary[:60])

    def add_bug_fix(self, description: str, metadata: dict[str, Any] | None = None) -> None:
        """Record a bug fix."""
        self._entries.append(
            ConversationEntry(
                event_type="bug_fix",
                summary=description,
                metadata=metadata or {},
            )
        )
        self._save()
        logger.debug("Conversation memory: recorded bug fix '%s'", description[:60])

    def add_action(self, description: str) -> None:
        """Record a recent action."""
        self._entries.append(
            ConversationEntry(event_type="action", summary=description)
        )
        self._save()

    def add_note(self, note: str) -> None:
        """Record a general note."""
        self._entries.append(
            ConversationEntry(event_type="note", summary=note)
        )
        self._save()

    def get_recent(self, n: int = 10, event_type: str | None = None) -> list[ConversationEntry]:
        """Return the *n* most recent entries, optionally filtered by type."""
        entries = self._entries
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]
        return entries[-n:]

    def search(self, query: str, n: int = 5) -> list[ConversationEntry]:
        """Simple keyword search across conversation summaries."""
        query_lower = query.lower()
        scored: list[tuple[float, ConversationEntry]] = []
        for entry in self._entries:
            text = entry.summary.lower()
            if query_lower in text:
                scored.append((1.0, entry))
            else:
                # Partial word matching
                words = query_lower.split()
                matches = sum(1 for w in words if w in text)
                if matches > 0:
                    scored.append((matches / len(words), entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:n]]

    def to_summary(self, n: int = 5) -> str:
        """Format recent entries as a prompt-friendly summary."""
        recent = self.get_recent(n)
        if not recent:
            return ""
        lines = ["Recent work:"]
        for entry in recent:
            lines.append(f"  [{entry.event_type}] {entry.summary}")
        return "\n".join(lines)

    def count(self) -> int:
        """Return total number of entries."""
        return len(self._entries)

    def clear(self) -> None:
        """Erase all conversation memory."""
        self._entries.clear()
        self._save()
