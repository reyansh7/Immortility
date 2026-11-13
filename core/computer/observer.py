"""ScreenObserver with screen hashing, caching, and perception versioning."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from core.computer.contracts import ComputerBackend, ScreenObservation, WindowInfo


class ScreenObserver:
    """Observes screen state, hashes screenshots, and caches observations."""

    def __init__(self, backend: ComputerBackend, cache_ttl_s: float = 2.0):
        self.backend = backend
        self.cache_ttl_s = cache_ttl_s
        self._cached_observation: ScreenObservation | None = None
        self._perception_version: int = 1

    def compute_hash(self, img_bytes: bytes | None, active_window_title: str) -> str:
        """Deterministic SHA256 hash used to detect exact screenshot/content changes."""
        if not img_bytes:
            return hashlib.sha256(f"empty|{active_window_title}".encode()).hexdigest()[:16]
        # Fast sampling hash: length + active_window + partial chunks
        h = hashlib.sha256()
        h.update(str(len(img_bytes)).encode())
        h.update(active_window_title.encode())
        h.update(img_bytes[:512])
        if len(img_bytes) > 2048:
            h.update(img_bytes[len(img_bytes) // 2 : (len(img_bytes) // 2) + 512])
            h.update(img_bytes[-512:])
        return h.hexdigest()[:16]

    async def observe_screen(
        self,
        force_fresh: bool = False,
        region: tuple[int, int, int, int] | None = None,
    ) -> ScreenObservation:
        """Observe the desktop screen and active window. Reuses cache if unchanged."""
        now = time.time()

        active_win = await self.backend.get_active_window()
        active_title = active_win.title if active_win else ""

        if not force_fresh and self._cached_observation is not None:
            # Check if TTL is still valid and active window title is identical
            cached = self._cached_observation
            if (now - cached.timestamp) < self.cache_ttl_s:
                cached_title = cached.active_window.title if cached.active_window else ""
                if cached_title == active_title:
                    return cached

        # Capture screenshot from backend
        img_bytes, width, height = await self.backend.take_screenshot(region=region)
        screen_hash = self.compute_hash(img_bytes, active_title)

        # Check if screen_hash is unchanged
        if self._cached_observation and self._cached_observation.screen_hash == screen_hash and not force_fresh:
            # Refresh timestamp on unchanged screen
            return self._cached_observation

        self._perception_version += 1
        obs = ScreenObservation(
            screenshot_bytes=img_bytes,
            screen_hash=screen_hash,
            width=width,
            height=height,
            active_window=active_win,
            timestamp=now,
            perception_version=self._perception_version,
        )
        self._cached_observation = obs
        return obs

    def invalidate_cache(self) -> None:
        """Explicitly invalidate perception cache upon a mutating computer action."""
        self._cached_observation = None
