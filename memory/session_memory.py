"""Ephemeral session memory — cleared when the application exits.

Stores temporary working data for the current run only.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SessionMemory:
    """In-memory key-value store that lives only for the current session.

    All data is lost when the process ends.  This is intentional —
    session memory is for transient working state that should not
    persist across restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        """Store a value."""
        self._store[key] = value
        logger.debug("Session memory set: %s", key)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value, returning *default* if missing."""
        return self._store.get(key, default)

    def delete(self, key: str) -> bool:
        """Remove a key. Returns True if the key existed."""
        if key in self._store:
            del self._store[key]
            logger.debug("Session memory deleted: %s", key)
            return True
        return False

    def has(self, key: str) -> bool:
        """Check whether a key exists."""
        return key in self._store

    def keys(self) -> list[str]:
        """Return all stored keys."""
        return list(self._store.keys())

    def clear(self) -> None:
        """Erase all session data."""
        count = len(self._store)
        self._store.clear()
        logger.info("Session memory cleared (%d entries).", count)

    def to_summary(self) -> str:
        """Short summary of session state for context injection."""
        if not self._store:
            return ""
        entries = [f"  {k}: {str(v)[:80]}" for k, v in self._store.items()]
        return "Session State:\n" + "\n".join(entries[:10])
