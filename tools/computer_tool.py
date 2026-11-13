"""Tool bindings exposing ComputerController capabilities to the IMMORTALITY ToolRegistry."""

from __future__ import annotations

from typing import Any

from core.computer.controller import ComputerController


class ComputerTool:
    """Provides native computer operating capabilities for Windows applications."""

    @staticmethod
    async def get_windows() -> dict[str, Any]:
        """List open visible top-level windows."""
        ctrl = ComputerController.get_instance()
        windows = await ctrl.get_windows()
        return {
            "status": "success",
            "windows": [w.to_dict() for w in windows],
            "total": len(windows),
        }

    @staticmethod
    async def get_active_window() -> dict[str, Any]:
        """Get the active window currently in foreground."""
        ctrl = ComputerController.get_instance()
        win = await ctrl.get_active_window()
        return {
            "status": "success",
            "active_window": win.to_dict() if win else None,
        }

    @staticmethod
    async def focus_window(title: str = "", handle: int = 0) -> dict[str, Any]:
        """Focus a window by title substring or HWND handle."""
        ctrl = ComputerController.get_instance()
        target = handle if handle > 0 else title
        res = await ctrl.focus_window(target)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
            "data": res.data,
            "error_class": res.error_class.value if res.error_class else None,
        }

    @staticmethod
    async def launch_application(name: str = "", path: str = "") -> dict[str, Any]:
        """Launch an application executable or workspace by name/path."""
        ctrl = ComputerController.get_instance()
        res = await ctrl.launch_application(name=name, path=path)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
            "data": res.data,
            "error_class": res.error_class.value if res.error_class else None,
        }

    @staticmethod
    async def close_window(title: str = "", handle: int = 0) -> dict[str, Any]:
        """Close an application window gracefully."""
        ctrl = ComputerController.get_instance()
        target = handle if handle > 0 else title
        res = await ctrl.close_window(target)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
            "error_class": res.error_class.value if res.error_class else None,
        }

    @staticmethod
    async def get_accessibility_tree(window_title: str = "", max_depth: int = 4) -> dict[str, Any]:
        """Retrieve the structured, filtered UI automation tree for active or named window."""
        ctrl = ComputerController.get_instance()
        tree = await ctrl.get_accessibility_tree(window_title=window_title, max_depth=max_depth)
        return {
            "status": "success",
            "tree": tree,
        }

    @staticmethod
    async def click(
        name: str = "",
        role: str = "",
        automation_id: str = "",
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        """Click an element by name/role/id or coordinate fallback."""
        ctrl = ComputerController.get_instance()
        coords = (int(x), int(y)) if x is not None and y is not None else None
        res = await ctrl.click(name=name, role=role, automation_id=automation_id, coords=coords)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
            "data": res.data,
            "error_class": res.error_class.value if res.error_class else None,
        }

    @staticmethod
    async def type_text(
        text: str,
        clear_first: bool = False,
        name: str = "",
        automation_id: str = "",
    ) -> dict[str, Any]:
        """Type text into target edit element via ValuePattern or active focused element."""
        ctrl = ComputerController.get_instance()
        res = await ctrl.type_text(
            text=text,
            clear_first=clear_first,
            name=name,
            automation_id=automation_id,
        )
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
            "data": res.data,
        }

    @staticmethod
    async def press_key(key: str) -> dict[str, Any]:
        """Press single keyboard key (e.g. 'enter', 'tab', 'escape')."""
        ctrl = ComputerController.get_instance()
        res = await ctrl.press_key(key=key)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
        }

    @staticmethod
    async def hotkey(keys: list[str]) -> dict[str, Any]:
        """Press key combination (e.g. ['ctrl', 's'])."""
        ctrl = ComputerController.get_instance()
        res = await ctrl.hotkey(keys=keys)
        return {
            "status": "success" if res.success else "error",
            "message": res.message,
        }

    @staticmethod
    async def screenshot(path: str = "") -> dict[str, Any]:
        """Capture desktop screenshot."""
        ctrl = ComputerController.get_instance()
        return await ctrl.screenshot(path=path)
