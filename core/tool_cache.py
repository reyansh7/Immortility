"""Session-scoped TTL cache for identical read-only tool calls."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

_DEFAULT_TTL = 120.0
_MAX = 64

_lock = threading.Lock()
_store: dict[str, tuple[float, str]] = {}


def _key(name: str, args: dict[str, Any] | None) -> str:
    raw = json.dumps({"t": name, "a": args or {}}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(name: str, args: dict[str, Any] | None, *, ttl: float = _DEFAULT_TTL) -> str | None:
    key = _key(name, args)
    now = time.time()
    with _lock:
        hit = _store.get(key)
        if not hit:
            return None
        ts, value = hit
        if now - ts > ttl:
            _store.pop(key, None)
            return None
        return value


def put(name: str, args: dict[str, Any] | None, value: str) -> None:
    key = _key(name, args)
    with _lock:
        if len(_store) >= _MAX:
            oldest = min(_store.items(), key=lambda item: item[1][0])[0]
            _store.pop(oldest, None)
        _store[key] = (time.time(), value)


def clear() -> None:
    with _lock:
        _store.clear()
