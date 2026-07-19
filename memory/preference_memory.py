"""Persistent preference memory — stores user preferences.

Data is stored as JSON in ``memory/data/preferences.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PREFERENCES_FILE = "memory/data/preferences.json"


class PreferenceMemory:
    """Stores and retrieves user preferences.

    Tracks:
    - Preferred language, framework, coding style
    - Browser, editor, hardware
    - Custom user-defined preferences
    """

    def __init__(self, filepath: str = PREFERENCES_FILE) -> None:
        self._path = Path(filepath)
        self._prefs: dict[str, Any] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._prefs = json.loads(
                self._path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Corrupt preference memory, starting fresh: %s", exc)
            self._prefs = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._prefs, indent=2), encoding="utf-8"
        )

    # ── Public API ──────────────────────────────────────────────────

    def set_preference(self, key: str, value: Any) -> None:
        """Store a preference.

        Args:
            key: Preference name (e.g. "language", "editor").
            value: Preference value.
        """
        self._prefs[key] = value
        self._save()
        logger.debug("Preference set: %s = %s", key, value)

    def set(self, key: str, value: Any) -> None:
        """Compatibility alias for simple key/value preference writes."""
        self.set_preference(key, value)

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Retrieve a preference value."""
        return self._prefs.get(key, default)

    def get_all(self) -> dict[str, Any]:
        """Return all preferences as a dictionary."""
        return dict(self._prefs)

    def delete_preference(self, key: str) -> bool:
        """Remove a preference. Returns True if it existed."""
        if key in self._prefs:
            del self._prefs[key]
            self._save()
            return True
        return False

    def to_summary(self) -> str:
        """Format preferences as a prompt-friendly summary."""
        if not self._prefs:
            return ""
        lines = ["User Preferences:"]
        for key, value in self._prefs.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def count(self) -> int:
        """Return number of stored preferences."""
        return len(self._prefs)

    def clear(self) -> None:
        """Erase all preferences."""
        self._prefs.clear()
        self._save()
