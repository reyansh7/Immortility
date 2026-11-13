"""Master ComputerController for IMMORTALITY Computer Use."""

from __future__ import annotations

import logging
import os
from typing import Any

from core.computer.contracts import (
    ActionResult,
    ComputerBackend,
    ComputerFailureClass,
    ElementInfo,
    ScreenObservation,
    WindowInfo,
)
from core.computer.failsafe import EmergencyStop
from core.computer.mock_backend import MockDesktopBackend
from core.computer.observer import ScreenObserver
from core.computer.tree_filter import AccessibilityTreeFilter
from core.computer.windows_backend import WindowsDesktopBackend
from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class ComputerController:
    """High-level controller coordinating backends, failsafe, perception, and actions."""

    _instance: ComputerController | None = None

    def __init__(
        self,
        backend: ComputerBackend | None = None,
        tree_filter: AccessibilityTreeFilter | None = None,
        observer: ScreenObserver | None = None,
        failsafe: EmergencyStop | None = None,
    ) -> None:
        if backend is not None:
            self.backend = backend
        else:
            self.backend = WindowsDesktopBackend() if os.name == "nt" else MockDesktopBackend()

        self.tree_filter = tree_filter or AccessibilityTreeFilter()
        self.observer = observer or ScreenObserver(self.backend)
        self.failsafe = failsafe or EmergencyStop()

    @classmethod
    def get_instance(cls) -> ComputerController:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set_instance(cls, controller: ComputerController | None) -> None:
        cls._instance = controller

    # ── Read-only inspection ──────────────────────────────────────────────────

    async def get_windows(self) -> list[WindowInfo]:
        """List all open top-level windows."""
        windows = await self.backend.get_windows()
        return windows

    async def get_active_window(self) -> WindowInfo | None:
        """Get the window currently in foreground."""
        return await self.backend.get_active_window()

    async def get_accessibility_tree(
        self,
        window_title: str = "",
        max_depth: int = 4,
    ) -> dict[str, Any]:
        """Fetch and filter accessibility tree into compact, actionable controls."""
        target_win = None
        if window_title:
            windows = await self.backend.get_windows()
            q = window_title.lower()
            for w in windows:
                if q in w.title.lower():
                    target_win = w
                    break
            if not target_win:
                return {
                    "status": "error",
                    "message": f"Window not found: '{window_title}'",
                    "error_class": ComputerFailureClass.WINDOW_NOT_FOUND.value,
                    "controls": [],
                }
        else:
            target_win = await self.backend.get_active_window()

        raw_tree = await self.backend.get_accessibility_tree(target_win, max_depth=max_depth)
        filtered = self.tree_filter.filter_tree(raw_tree)
        if target_win:
            filtered["window_title"] = target_win.title
            filtered["window_handle"] = target_win.handle
        return filtered

    async def observe_screen(self, force_fresh: bool = False) -> ScreenObservation:
        """Capture cached or fresh screen perception."""
        return await self.observer.observe_screen(force_fresh=force_fresh)

    # ── Mutating computer actions (gated by EmergencyStop) ─────────────────────

    async def launch_application(self, name: str, path: str = "") -> ActionResult:
        """Launch an application and verify window manifestation."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "launch_application", "target": name or path})
        res = await self.backend.launch_application(name, path)

        if res.success:
            active = await self.backend.get_active_window()
            if active:
                EventBus().publish("computer.window_changed", {"window": active.to_dict()})

        EventBus().publish("computer.action_completed", {"action": "launch_application", "success": res.success})
        return res

    async def focus_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Focus a window by title, handle, or WindowInfo."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "focus_window", "target": str(target)})
        res = await self.backend.focus_window(target)

        if res.success:
            active = await self.backend.get_active_window()
            if active:
                EventBus().publish("computer.window_changed", {"window": active.to_dict()})

        EventBus().publish("computer.action_completed", {"action": "focus_window", "success": res.success})
        return res

    async def close_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Close a window."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "close_window", "target": str(target)})
        res = await self.backend.close_window(target)
        EventBus().publish("computer.action_completed", {"action": "close_window", "success": res.success})
        return res

    async def click(
        self,
        name: str = "",
        role: str = "",
        automation_id: str = "",
        coords: tuple[int, int] | None = None,
        element: ElementInfo | None = None,
    ) -> ActionResult:
        """Click a logical element in the active window or fallback coordinates.

        Control hierarchy:
          1. UI Automation control patterns (Invoke, Toggle, SelectionItem, ExpandCollapse)
          2. Coordinate mouse click fallback
        """
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "click", "target": name or (element.name if element else coords)})

        active_win = await self.backend.get_active_window()
        raw_tree = await self.backend.get_accessibility_tree(active_win)

        element_match: ElementInfo | None = None

        # Check for stale element reference if element object was provided
        if element is not None:
            if self.tree_filter.is_stale(element, raw_tree):
                logger.warning("Stale element detected for '%s'; re-observing and re-grounding...", element.name)
                # Bounded retry: refresh accessibility tree and re-ground
                raw_tree = await self.backend.get_accessibility_tree(active_win)
                regrounded = self.tree_filter.find_element(
                    raw_tree,
                    name=element.name,
                    role=element.role,
                    automation_id=element.automation_id or "",
                )
                if not regrounded and not coords:
                    res = ActionResult(
                        success=False,
                        message=f"Element '{element.name}' became stale and could not be regrounded",
                        error_class=ComputerFailureClass.STALE_ELEMENT,
                    )
                    EventBus().publish("computer.action_completed", {"action": "click", "success": False})
                    return res
                element_match = regrounded
            else:
                element_match = element

        # Ground element logically if criteria provided
        elif name or role or automation_id:
            element_match = self.tree_filter.find_element(
                raw_tree,
                name=name,
                role=role,
                automation_id=automation_id,
            )
            if not element_match and not coords:
                res = ActionResult(
                    success=False,
                    message=f"Element not found matching name='{name}', role='{role}', id='{automation_id}'",
                    error_class=ComputerFailureClass.CONTROL_NOT_FOUND,
                )
                EventBus().publish("computer.action_completed", {"action": "click", "success": False})
                return res

        # Control Hierarchy Level 4: UI Automation pattern invocation
        if element_match and element_match.supported_patterns:
            clean_patterns = [p.replace("Pattern", "").lower() for p in element_match.supported_patterns]
            pattern_res: ActionResult | None = None
            if "invoke" in clean_patterns or element_match.role in ("button", "menuitem", "splitbutton", "hyperlink"):
                pattern_res = await self.backend.invoke_pattern(element_match, "InvokePattern")
            elif "toggle" in clean_patterns or element_match.role in ("checkbox", "radiobutton"):
                pattern_res = await self.backend.invoke_pattern(element_match, "TogglePattern")
            elif "selectionitem" in clean_patterns or element_match.role in ("tabitem", "listitem"):
                pattern_res = await self.backend.invoke_pattern(element_match, "SelectionItemPattern")
            elif "expandcollapse" in clean_patterns or element_match.role in ("combobox", "treeitem"):
                pattern_res = await self.backend.invoke_pattern(element_match, "ExpandCollapsePattern")

            if pattern_res and pattern_res.success:
                EventBus().publish("computer.action_completed", {"action": "click", "success": True, "method": "uia_pattern"})
                return pattern_res

        # Control Hierarchy Level 6: Mouse coordinate fallback
        fallback_coords = coords
        if not fallback_coords and element_match:
            l, t, r, b = element_match.bounds
            fallback_coords = ((l + r) // 2, (t + b) // 2)

        res = await self.backend.click_element(element=element_match, coords=fallback_coords)
        EventBus().publish("computer.action_completed", {"action": "click", "success": res.success, "method": "coordinate_fallback"})
        return res

    async def type_text(
        self,
        text: str,
        clear_first: bool = False,
        name: str = "",
        automation_id: str = "",
    ) -> ActionResult:
        """Type text into target edit element via ValuePattern or active focused element via keyboard."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "type_text", "len": len(text)})

        # Hierarchy Level 4: UI Automation ValuePattern if target element specified
        if name or automation_id:
            active_win = await self.backend.get_active_window()
            raw_tree = await self.backend.get_accessibility_tree(active_win)
            target_elem = self.tree_filter.find_element(raw_tree, name=name, automation_id=automation_id)
            if target_elem and ("ValuePattern" in target_elem.supported_patterns or "value" in [p.lower() for p in target_elem.supported_patterns] or target_elem.role in ("edit", "textbox")):
                val_res = await self.backend.invoke_pattern(target_elem, "ValuePattern", value=text)
                if val_res.success:
                    EventBus().publish("computer.action_completed", {"action": "type_text", "success": True, "method": "uia_value_pattern"})
                    return val_res

        # Hierarchy Level 6: Keyboard event fallback
        res = await self.backend.type_text(text, clear_first=clear_first)
        EventBus().publish("computer.action_completed", {"action": "type_text", "success": res.success, "method": "keyboard_fallback"})
        return res

    async def press_key(self, key: str) -> ActionResult:
        """Press single keyboard key."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "press_key", "key": key})
        res = await self.backend.press_key(key)
        EventBus().publish("computer.action_completed", {"action": "press_key", "success": res.success})
        return res

    async def hotkey(self, keys: list[str]) -> ActionResult:
        """Press keyboard key combination."""
        self.failsafe.check_or_raise()
        self.observer.invalidate_cache()

        EventBus().publish("computer.action_started", {"action": "hotkey", "keys": keys})
        res = await self.backend.hotkey(keys)
        EventBus().publish("computer.action_completed", {"action": "hotkey", "success": res.success})
        return res

    async def screenshot(self, path: str = "") -> dict[str, Any]:
        """Capture screenshot and optionally save to path."""
        obs = await self.observe_screen(force_fresh=True)
        saved_path = ""
        if path and obs.screenshot_bytes:
            from pathlib import Path
            p = Path(path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(obs.screenshot_bytes)
            saved_path = str(p)

        return {
            "status": "success",
            "screen_hash": obs.screen_hash,
            "width": obs.width,
            "height": obs.height,
            "active_window": obs.active_window.to_dict() if obs.active_window else None,
            "saved_to": saved_path,
        }
