"""LRU retrieval cache with automatic invalidation.

Caches recent searches, retrieved chunks, and project metadata.
Entries are automatically invalidated when indexed files change.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 128
DEFAULT_TTL_SECONDS = 300  # 5 minutes


@dataclass
class CacheEntry:
    """A single cached retrieval result."""

    key: str
    value: Any
    timestamp: float = field(default_factory=time.time)

    def is_expired(self, ttl: float) -> bool:
        """Check whether this entry has exceeded its TTL."""
        return (time.time() - self.timestamp) > ttl


class RetrievalCache:
    """Thread-safe LRU cache for retrieval results.

    Parameters:
        max_size: Maximum number of entries.
        ttl_seconds: Time-to-live for each entry (seconds).
    """

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._invalidated_files: set[str] = set()

    # ── Public API ──────────────────────────────────────────────────

    def get(self, query: str, project: str = "") -> Any | None:
        """Retrieve a cached result for *query*, or ``None`` on miss.

        Args:
            query: The search query.
            project: Project scope.

        Returns:
            Cached value or ``None``.
        """
        key = self._make_key(query, project)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired(self._ttl):
                del self._cache[key]
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            logger.debug("Cache hit: %s", key[:32])
            return entry.value

    def put(self, query: str, project: str, value: Any) -> None:
        """Store a result in the cache.

        Args:
            query: The search query.
            project: Project scope.
            value: The result to cache.
        """
        key = self._make_key(query, project)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = CacheEntry(key=key, value=value)
            else:
                if len(self._cache) >= self._max_size:
                    self._cache.popitem(last=False)  # Evict LRU
                self._cache[key] = CacheEntry(key=key, value=value)

    def invalidate_file(self, filepath: str) -> None:
        """Mark a file as changed, invalidating all related cache entries.

        Args:
            filepath: The changed file path.
        """
        with self._lock:
            self._invalidated_files.add(filepath)
            # Remove all entries whose results reference this file
            keys_to_remove: list[str] = []
            for key, entry in self._cache.items():
                if self._references_file(entry.value, filepath):
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self._cache[key]
            if keys_to_remove:
                logger.debug(
                    "Invalidated %d cache entries for %s",
                    len(keys_to_remove),
                    filepath,
                )

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            self._cache.clear()
            self._invalidated_files.clear()
            logger.debug("Cache fully invalidated.")

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "invalidated_files": len(self._invalidated_files),
            }

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _make_key(query: str, project: str) -> str:
        """Deterministic cache key from query + project."""
        from core.text_sanitize import sanitize_text
        safe_q = sanitize_text(query)
        safe_p = sanitize_text(project)
        raw = f"{safe_p}::{safe_q}".encode("utf-8", errors="replace")
        return hashlib.md5(raw).hexdigest()

    @staticmethod
    def _references_file(value: Any, filepath: str) -> bool:
        """Check whether a cached value references *filepath*."""
        if isinstance(value, list):
            for item in value:
                fn = ""
                if hasattr(item, "filename"):
                    fn = item.filename
                elif isinstance(item, dict):
                    fn = item.get("filename", "")
                if fn and filepath in fn:
                    return True
        return False
