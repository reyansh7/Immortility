"""UserModel — First-class personal intelligence representation for IMMORTALITY.

Aggregates:
  - User identity and profile facts
  - Stable preferences
  - Working style and communication constraints
  - Active and recent projects
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from memory.preference_memory import PreferenceMemory
from memory.user_profile import UserProfile

logger = logging.getLogger(__name__)


@dataclass
class UserModel:
    """Represents the user's identity, preferences, and operating profile."""

    name: str = "User"
    role: str = ""
    preferred_language: str = "python"
    communication_style: str = "concise"
    preferences: dict[str, Any] = field(default_factory=dict)
    active_projects: list[str] = field(default_factory=list)

    _instance: UserModel | None = None

    @classmethod
    def get_instance(cls) -> UserModel:
        """Load user model from underlying persistent profile and preference stores."""
        if cls._instance is None:
            cls._instance = cls.load()
        return cls._instance

    @classmethod
    def load(cls) -> UserModel:
        try:
            profile = UserProfile()
            name = profile.get_name() or "User"
            role = profile.get_role() or ""
        except Exception:
            name = "User"
            role = ""

        prefs = {}
        try:
            pref_store = PreferenceMemory()
            prefs = pref_store.get_all()
        except Exception:
            pass

        return UserModel(
            name=name,
            role=role,
            preferred_language=str(prefs.get("preferred_language", "python")),
            communication_style=str(prefs.get("communication_style", "concise")),
            preferences=prefs,
        )

    def get_context_summary(self) -> str:
        """Format a token-budgeted summary for system prompt injection."""
        lines = [f"Name: {self.name}"]
        if self.role:
            lines.append(f"Role: {self.role}")
        if self.preferred_language:
            lines.append(f"Preferred Language: {self.preferred_language}")
        if self.communication_style:
            lines.append(f"Style: {self.communication_style}")

        # Add top stable preferences
        if self.preferences:
            custom_prefs = [f"{k}={v}" for k, v in self.preferences.items() if k not in {"name", "role"}]
            if custom_prefs:
                lines.append(f"Preferences: {', '.join(custom_prefs[:5])}")

        return "\n".join(lines)
