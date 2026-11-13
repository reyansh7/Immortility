"""Local, deterministic Emergency Stop mechanism for Computer Use.

Independent of LLM reasoning:
  - Immediately halts in-flight computer operations
  - Blocks subsequent computer mutations
  - Records emergency event on EventBus
  - Requires explicit manual reset
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class EmergencyStop:
    """Singleton emergency stop controller."""

    _instance: EmergencyStop | None = None
    _lock = threading.Lock()

    def __new__(cls) -> EmergencyStop:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._is_stopped = False
                cls._reason = ""
                cls._stopped_at = 0.0
        return cls._instance

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped

    @property
    def reason(self) -> str:
        return self._reason

    def trigger(self, reason: str = "Emergency stop triggered by user") -> None:
        """Trigger immediate emergency stop. Blocks all future computer mutations."""
        import time
        with self._lock:
            self._is_stopped = True
            self._reason = reason
            self._stopped_at = time.time()
        logger.critical("EMERGENCY STOP ACTIVATED: %s", reason)
        try:
            EventBus().publish("computer.emergency_stop", {"reason": reason, "timestamp": self._stopped_at})
        except Exception:
            pass

    def reset(self) -> None:
        """Reset emergency stop to allow operations after manual review."""
        with self._lock:
            self._is_stopped = False
            self._reason = ""
            self._stopped_at = 0.0
        logger.info("EMERGENCY STOP CLEARED")
        try:
            EventBus().publish("computer.emergency_stop_cleared", {})
        except Exception:
            pass

    def check_or_raise(self) -> None:
        """Raise PermissionError if emergency stop is currently active."""
        if self._is_stopped:
            raise PermissionError(f"Computer actions are blocked by EMERGENCY STOP: {self._reason}")
