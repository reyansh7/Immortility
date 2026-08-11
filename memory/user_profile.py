"""User profile — name and preferences (no hardcoded personal names in prompts)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _profile_path() -> Path:
    try:
        from core.repo_paths import user_profile_path

        return user_profile_path()
    except Exception:
        return Path("memory/data/user_profile.json")


class UserProfile:
    """Lightweight profile loaded from disk / preferences."""

    def __init__(self, filepath: str | Path | None = None) -> None:
        self._path = Path(filepath) if filepath else _profile_path()
        import os

        default_name = (os.environ.get("IMMORTILITY_USER_NAME") or "").strip() or "there"
        self._data: dict[str, Any] = {
            "name": default_name,
            "preferred_language": "",
            "preferred_framework": "",
            "common_projects": [],
        }
        self._load()

    def _load(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data.update(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug("user profile load failed: %s", exc)
        # Overlay preference memory if present
        try:
            from memory.preference_memory import PreferenceMemory

            prefs = PreferenceMemory().get_all()
            if prefs.get("user_name"):
                self._data["name"] = str(prefs["user_name"])
            if prefs.get("preferred_language"):
                self._data["preferred_language"] = str(prefs["preferred_language"])
            if prefs.get("preferred_framework"):
                self._data["preferred_framework"] = str(prefs["preferred_framework"])
        except Exception:
            pass

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def name(self) -> str:
        return str(self._data.get("name") or "there")

    @name.setter
    def name(self, value: str) -> None:
        self._data["name"] = (value or "").strip() or "there"
        self.save()

    def maybe_learn_from_text(self, text: str) -> None:
        """If user says 'my name is X', store it."""
        m = re.search(
            r"\b(?:my name is|call me|i am|i'm)\s+([A-Z][a-zA-Z]{1,30})\b",
            text or "",
            re.I,
        )
        if m:
            self.name = m.group(1)


def get_user_name() -> str:
    return UserProfile().name
