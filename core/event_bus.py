"""Lightweight in-process event bus for workflow coordination."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    _instance: "EventBus | None" = None

    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers = defaultdict(list)
        return cls._instance

    def subscribe(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        for handler in self._handlers.get(event_type, []):
            try:
                handler(payload)
            except Exception as exc:
                logger.warning("Event handler error for %s: %s", event_type, exc)

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
