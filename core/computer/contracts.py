"""Contracts, data models, and protocols for IMMORTALITY Computer Use."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ComputerFailureClass(str, Enum):
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    CONTROL_NOT_FOUND = "CONTROL_NOT_FOUND"
    CONTROL_DISABLED = "CONTROL_DISABLED"
    APPLICATION_NOT_FOUND = "APPLICATION_NOT_FOUND"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    STALE_ELEMENT = "STALE_ELEMENT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    EMERGENCY_STOP_TRIGGERED = "EMERGENCY_STOP_TRIGGERED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    UNKNOWN_COMPUTER_ERROR = "UNKNOWN_COMPUTER_ERROR"


@dataclass(frozen=True)
class WindowInfo:
    """Represents a top-level native OS window."""

    handle: int
    title: str
    pid: int
    class_name: str
    rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    is_visible: bool = True
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "pid": self.pid,
            "class_name": self.class_name,
            "rect": list(self.rect),
            "is_visible": self.is_visible,
            "is_active": self.is_active,
        }


@dataclass(frozen=True)
class ElementInfo:
    """Represents a structured UI element in the accessibility tree."""

    id: str
    role: str  # button, edit, checkbox, text, window, list, tree, menu, etc.
    name: str
    bounds: tuple[int, int, int, int]  # (left, top, right, bottom)
    is_enabled: bool = True
    is_focused: bool = False
    value: str | None = None
    automation_id: str | None = None
    supported_patterns: tuple[str, ...] = field(default_factory=tuple)
    generation_id: int = 1
    timestamp: float = field(default_factory=time.time)
    children: tuple[ElementInfo, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "bounds": list(self.bounds),
            "is_enabled": self.is_enabled,
            "is_focused": self.is_focused,
            "generation_id": self.generation_id,
            "timestamp": self.timestamp,
        }
        if self.value is not None:
            data["value"] = self.value
        if self.automation_id:
            data["automation_id"] = self.automation_id
        if self.supported_patterns:
            data["supported_patterns"] = list(self.supported_patterns)
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


@dataclass(frozen=True)
class ActionResult:
    """Standard outcome from a computer action."""

    success: bool
    message: str
    error_class: ComputerFailureClass | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "error_class": self.error_class.value if self.error_class else None,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ScreenObservation:
    """Structured perception output representing screen state."""

    screenshot_bytes: bytes | None
    screen_hash: str
    width: int
    height: int
    active_window: WindowInfo | None
    timestamp: float = field(default_factory=time.time)
    perception_version: int = 1

    def to_dict(self, include_bytes: bool = False) -> dict[str, Any]:
        res: dict[str, Any] = {
            "screen_hash": self.screen_hash,
            "width": self.width,
            "height": self.height,
            "active_window": self.active_window.to_dict() if self.active_window else None,
            "timestamp": self.timestamp,
            "perception_version": self.perception_version,
        }
        if include_bytes and self.screenshot_bytes:
            res["screenshot_bytes_len"] = len(self.screenshot_bytes)
        return res


@runtime_checkable
class ComputerBackend(Protocol):
    """Low-level interface for operating system window and desktop interaction."""

    async def get_windows(self) -> list[WindowInfo]:
        """Enumerate all open top-level windows."""
        ...

    async def get_active_window(self) -> WindowInfo | None:
        """Return the window currently in foreground."""
        ...

    async def focus_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Bring a window to foreground."""
        ...

    async def launch_application(self, name: str, path: str = "") -> ActionResult:
        """Launch an application executable or workspace."""
        ...

    async def close_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Close an application window."""
        ...

    async def get_accessibility_tree(
        self,
        window: WindowInfo | None = None,
        max_depth: int = 4,
    ) -> ElementInfo | None:
        """Extract structured UI automation tree for active or specified window."""
        ...

    async def click_element(
        self,
        element: ElementInfo | None = None,
        coords: tuple[int, int] | None = None,
    ) -> ActionResult:
        """Click an element or fallback coordinate."""
        ...

    async def type_text(self, text: str, clear_first: bool = False) -> ActionResult:
        """Type text into currently focused element."""
        ...

    async def press_key(self, key: str) -> ActionResult:
        """Press a keyboard key."""
        ...

    async def hotkey(self, keys: list[str]) -> ActionResult:
        """Press combination of keys (e.g. ['ctrl', 's'])."""
        ...

    async def move_mouse(self, x: int, y: int) -> ActionResult:
        """Move mouse cursor to coordinates."""
        ...

    async def take_screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bytes | None, int, int]:
        """Capture screenshot as PNG bytes with dimensions."""
        ...

    async def invoke_pattern(
        self,
        element: ElementInfo,
        pattern_name: str,
        value: Any = None,
    ) -> ActionResult:
        """Invoke a native UI Automation control pattern (e.g. Invoke, Value, Toggle, ExpandCollapse, SelectionItem, Scroll)."""
        ...


@runtime_checkable
class VisionProvider(Protocol):
    """Future visual perception expansion point (e.g. OmniParser / local VLM)."""

    async def analyze_screen(
        self,
        screenshot: bytes,
        prompt: str,
    ) -> dict[str, Any]:
        """Analyze visual contents and ground elements visually."""
        ...
