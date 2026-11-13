"""Mock desktop backend for deterministic, hermetic testing of computer use."""

from __future__ import annotations

import io
from typing import Any

from core.computer.contracts import (
    ActionResult,
    ComputerBackend,
    ComputerFailureClass,
    ElementInfo,
    WindowInfo,
)


def _generate_synthetic_png(width: int = 1920, height: int = 1080) -> bytes:
    """Generate minimal valid PNG bytes for testing without external tools."""
    try:
        from PIL import Image
        img = Image.new("RGB", (width // 10, height // 10), color=(30, 30, 30))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Fallback 1x1 PNG bytes
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


class MockDesktopBackend(ComputerBackend):
    """In-memory simulation of a Windows desktop for hermetic test execution."""

    def __init__(self, initial_windows: list[WindowInfo] | None = None):
        self.windows: dict[int, WindowInfo] = {}
        self.active_handle: int = 0
        self.trees: dict[int, ElementInfo] = {}
        self.typed_history: list[str] = []
        self.clicked_history: list[str] = []
        self.hotkey_history: list[list[str]] = []
        self.launched_history: list[str] = []
        self.patterns_invoked: list[tuple[str, str, Any]] = []

        # Fault injection switches for recovery testing
        self.fail_next_click: ComputerFailureClass | None = None
        self.fail_next_launch: ComputerFailureClass | None = None
        self.fail_pattern: dict[str, ComputerFailureClass] = {}
        self.delay_action_s: float = 0.0
        self.generation_id: int = 1

        if initial_windows:
            for w in initial_windows:
                self.windows[w.handle] = w
                if w.is_active:
                    self.active_handle = w.handle
        else:
            self._setup_default_windows()

    def _setup_default_windows(self) -> None:
        """Create standard default desktop state."""
        w1 = WindowInfo(
            handle=1001,
            title="Visual Studio Code - Forge",
            pid=4200,
            class_name="Chrome_WidgetWin_1",
            rect=(50, 50, 1800, 1000),
            is_visible=True,
            is_active=True,
        )
        w2 = WindowInfo(
            handle=1002,
            title="Google Chrome - GitHub",
            pid=5120,
            class_name="Chrome_WidgetWin_1",
            rect=(100, 100, 1600, 900),
            is_visible=True,
            is_active=False,
        )
        w3 = WindowInfo(
            handle=1003,
            title="Windows PowerShell",
            pid=6890,
            class_name="ConsoleWindowClass",
            rect=(200, 200, 1000, 700),
            is_visible=True,
            is_active=False,
        )
        self.windows = {w.handle: w for w in (w1, w2, w3)}
        self.active_handle = 1001

        # Accessibility tree for VS Code with patterns
        run_btn = ElementInfo(
            id="vscode_btn_run",
            role="button",
            name="Run",
            bounds=(300, 70, 360, 100),
            automation_id="workbench.action.debug.start",
            supported_patterns=("InvokePattern", "ScrollItemPattern"),
            generation_id=self.generation_id,
        )
        explorer_tree = ElementInfo(
            id="vscode_tree_explorer",
            role="tree",
            name="Explorer",
            bounds=(50, 100, 250, 900),
            automation_id="workbench.view.explorer",
            supported_patterns=("ExpandCollapsePattern", "SelectionPattern"),
            generation_id=self.generation_id,
        )
        terminal_tab = ElementInfo(
            id="vscode_tab_terminal",
            role="tabitem",
            name="Terminal",
            bounds=(300, 600, 400, 630),
            automation_id="workbench.action.terminal.toggleTerminal",
            supported_patterns=("SelectionItemPattern",),
            generation_id=self.generation_id,
        )
        search_edit = ElementInfo(
            id="vscode_edit_search",
            role="edit",
            name="Search",
            bounds=(50, 60, 250, 90),
            automation_id="search.input",
            supported_patterns=("ValuePattern",),
            generation_id=self.generation_id,
        )
        wrap_toggle = ElementInfo(
            id="vscode_chk_wrap",
            role="checkbox",
            name="Word Wrap",
            bounds=(100, 700, 150, 720),
            automation_id="editor.wordWrap",
            supported_patterns=("TogglePattern",),
            generation_id=self.generation_id,
        )
        root = ElementInfo(
            id="vscode_root",
            role="window",
            name="Visual Studio Code - Forge",
            bounds=(50, 50, 1800, 1000),
            generation_id=self.generation_id,
            children=(run_btn, explorer_tree, terminal_tab, search_edit, wrap_toggle),
        )
        self.trees[1001] = root

    async def get_windows(self) -> list[WindowInfo]:
        return list(self.windows.values())

    async def get_active_window(self) -> WindowInfo | None:
        return self.windows.get(self.active_handle)

    async def focus_window(self, target: WindowInfo | int | str) -> ActionResult:
        target_handle = None
        if isinstance(target, WindowInfo):
            target_handle = target.handle
        elif isinstance(target, int):
            target_handle = target
        elif isinstance(target, str):
            t_low = target.lower()
            for w in self.windows.values():
                if t_low in w.title.lower():
                    target_handle = w.handle
                    break

        if target_handle is None or target_handle not in self.windows:
            return ActionResult(
                success=False,
                message=f"Window not found matching '{target}'",
                error_class=ComputerFailureClass.WINDOW_NOT_FOUND,
            )

        # Update previous and new active flags
        if self.active_handle in self.windows:
            old = self.windows[self.active_handle]
            self.windows[self.active_handle] = WindowInfo(
                handle=old.handle,
                title=old.title,
                pid=old.pid,
                class_name=old.class_name,
                rect=old.rect,
                is_visible=old.is_visible,
                is_active=False,
            )

        cur = self.windows[target_handle]
        self.windows[target_handle] = WindowInfo(
            handle=cur.handle,
            title=cur.title,
            pid=cur.pid,
            class_name=cur.class_name,
            rect=cur.rect,
            is_visible=True,
            is_active=True,
        )
        self.active_handle = target_handle
        return ActionResult(
            success=True,
            message=f"Focused window: {cur.title}",
            data={"handle": cur.handle, "title": cur.title},
        )

    async def launch_application(self, name: str, path: str = "") -> ActionResult:
        if self.fail_next_launch:
            err = self.fail_next_launch
            self.fail_next_launch = None
            return ActionResult(success=False, message="Application launch failed", error_class=err)

        self.launched_history.append(name or path)
        title = f"{name or 'App'}"
        if "code" in (name or "").lower() or "vscode" in (name or "").lower():
            title = "Visual Studio Code"
            if path:
                title += f" - {path}"

        new_handle = max(self.windows.keys(), default=1000) + 1
        new_win = WindowInfo(
            handle=new_handle,
            title=title,
            pid=7000 + new_handle,
            class_name="ApplicationFrameWindow",
            rect=(100, 100, 1200, 800),
            is_visible=True,
            is_active=True,
        )
        self.windows[new_handle] = new_win
        self.active_handle = new_handle
        return ActionResult(
            success=True,
            message=f"Launched {title}",
            data={"handle": new_handle, "title": title},
        )

    async def close_window(self, target: WindowInfo | int | str) -> ActionResult:
        target_handle = None
        if isinstance(target, WindowInfo):
            target_handle = target.handle
        elif isinstance(target, int):
            target_handle = target
        elif isinstance(target, str):
            t_low = target.lower()
            for w in self.windows.values():
                if t_low in w.title.lower():
                    target_handle = w.handle
                    break

        if target_handle is None or target_handle not in self.windows:
            return ActionResult(
                success=False,
                message=f"Window not found: {target}",
                error_class=ComputerFailureClass.WINDOW_NOT_FOUND,
            )

        closed = self.windows.pop(target_handle)
        if self.active_handle == target_handle:
            self.active_handle = next(iter(self.windows.keys()), 0)
        return ActionResult(success=True, message=f"Closed window: {closed.title}")

    async def get_accessibility_tree(
        self,
        window: WindowInfo | None = None,
        max_depth: int = 4,
    ) -> ElementInfo | None:
        win = window or await self.get_active_window()
        if not win:
            return None
        return self.trees.get(win.handle)

    async def click_element(
        self,
        element: ElementInfo | None = None,
        coords: tuple[int, int] | None = None,
    ) -> ActionResult:
        if self.fail_next_click:
            err = self.fail_next_click
            self.fail_next_click = None
            return ActionResult(success=False, message="Click failed", error_class=err)

        if coords:
            self.clicked_history.append(f"coords:{coords[0]},{coords[1]}")
            return ActionResult(
                success=True,
                message=f"Clicked coordinates {coords}",
                data={"coords": list(coords), "element_id": element.id if element else None},
            )
        if element:
            self.clicked_history.append(f"{element.role}:{element.name}")
            return ActionResult(
                success=True,
                message=f"Clicked element '{element.name}' ({element.role})",
                data={"element_id": element.id, "bounds": list(element.bounds)},
            )
        return ActionResult(
            success=False,
            message="No element or coordinates provided to click",
            error_class=ComputerFailureClass.CONTROL_NOT_FOUND,
        )

    async def type_text(self, text: str, clear_first: bool = False) -> ActionResult:
        self.typed_history.append(text)
        return ActionResult(
            success=True,
            message=f"Typed text into active element",
            data={"text_len": len(text)},
        )

    async def press_key(self, key: str) -> ActionResult:
        return ActionResult(success=True, message=f"Pressed key: {key}")

    async def hotkey(self, keys: list[str]) -> ActionResult:
        self.hotkey_history.append(list(keys))
        return ActionResult(success=True, message=f"Dispatched hotkey: {'+'.join(keys)}")

    async def move_mouse(self, x: int, y: int) -> ActionResult:
        return ActionResult(success=True, message=f"Moved mouse to ({x}, {y})")

    async def take_screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bytes | None, int, int]:
        png = _generate_synthetic_png(1920, 1080)
        return png, 1920, 1080

    async def invoke_pattern(
        self,
        element: ElementInfo,
        pattern_name: str,
        value: Any = None,
    ) -> ActionResult:
        """Simulate native UI Automation control pattern invocation."""
        if pattern_name in self.fail_pattern:
            err = self.fail_pattern[pattern_name]
            return ActionResult(
                success=False,
                message=f"Pattern invocation failed for '{pattern_name}' on '{element.name}'",
                error_class=err,
            )

        p_clean = pattern_name.replace("Pattern", "").lower()
        supported_clean = [p.replace("Pattern", "").lower() for p in element.supported_patterns]
        if supported_clean and p_clean not in supported_clean and "all" not in supported_clean:
            return ActionResult(
                success=False,
                message=f"Pattern '{pattern_name}' not supported by element '{element.name}'",
                error_class=ComputerFailureClass.CONTROL_DISABLED,
            )

        self.patterns_invoked.append((element.id, pattern_name, value))

        if p_clean == "value" and value is not None:
            self.typed_history.append(str(value))
        elif p_clean in ("invoke", "toggle", "selectionitem"):
            self.clicked_history.append(f"pattern:{pattern_name}:{element.role}:{element.name}")

        return ActionResult(
            success=True,
            message=f"Invoked {pattern_name} on '{element.name}' ({element.role})",
            data={"element_id": element.id, "pattern": pattern_name, "value": value},
        )

    def update_tree(self, handle: int, root: ElementInfo) -> None:
        """Update accessibility tree and advance generation ID for stale element testing."""
        self.generation_id += 1
        self.trees[handle] = root
