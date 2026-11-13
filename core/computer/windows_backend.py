"""Live Windows desktop backend using ctypes user32, GDI, and UI Automation."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import time
from typing import Any

from core.computer.contracts import (
    ActionResult,
    ComputerBackend,
    ComputerFailureClass,
    ElementInfo,
    WindowInfo,
)

logger = logging.getLogger(__name__)

# Key codes for Windows virtual keys
VK_MAP: dict[str, int] = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "escape": 0x1B,
    "esc": 0x1B,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
WM_CLOSE = 0x0010


class WindowsDesktopBackend(ComputerBackend):
    """Native Windows implementation using ctypes user32, GDI, and UI Automation."""

    def __init__(self) -> None:
        self._user32: Any = None
        if os.name == "nt":
            import ctypes
            self._user32 = ctypes.windll.user32

    def _attach_thread_desktop(self) -> None:
        """Attach current thread to interactive default desktop if available."""
        if not self._user32:
            return
        try:
            hdesk = self._user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hdesk:
                self._user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

    def _get_window_title(self, hwnd: int) -> str:
        if not self._user32:
            return ""
        import ctypes
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buff = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value

    def _get_window_class(self, hwnd: int) -> str:
        if not self._user32:
            return ""
        import ctypes
        buff = ctypes.create_unicode_buffer(256)
        self._user32.GetClassNameW(hwnd, buff, 256)
        return buff.value

    def _get_window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        if not self._user32:
            return (0, 0, 0, 0)
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    def _get_window_pid(self, hwnd: int) -> int:
        if not self._user32:
            return 0
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    async def get_windows(self) -> list[WindowInfo]:
        """Enumerate visible top-level windows."""
        if not self._user32:
            return []

        self._attach_thread_desktop()

        import ctypes
        from ctypes import wintypes

        windows: list[WindowInfo] = []
        active_hwnd = self._user32.GetForegroundWindow()

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_proc(hwnd: int, lparam: int) -> bool:
            if not self._user32.IsWindowVisible(hwnd):
                return True
            title = self._get_window_title(hwnd)
            if not title:
                return True  # Ignore titleless invisible helpers
            cls_name = self._get_window_class(hwnd)
            rect = self._get_window_rect(hwnd)
            pid = self._get_window_pid(hwnd)

            windows.append(
                WindowInfo(
                    handle=hwnd,
                    title=title,
                    pid=pid,
                    class_name=cls_name,
                    rect=rect,
                    is_visible=True,
                    is_active=(hwnd == active_hwnd),
                )
            )
            return True

        cb = WNDENUMPROC(_enum_proc)
        self._user32.EnumWindows(cb, 0)
        if not windows:
            # Fallback to desktop window enumeration on default desktop
            try:
                hdesk = self._user32.OpenDesktopW("default", 0, False, 0x01FF)
                if hdesk:
                    self._user32.EnumDesktopWindows(hdesk, cb, 0)
            except Exception:
                pass
        return windows

    async def get_active_window(self) -> WindowInfo | None:
        """Return the foreground window."""
        if not self._user32:
            return None
        self._attach_thread_desktop()
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        title = self._get_window_title(hwnd)
        return WindowInfo(
            handle=hwnd,
            title=title,
            pid=self._get_window_pid(hwnd),
            class_name=self._get_window_class(hwnd),
            rect=self._get_window_rect(hwnd),
            is_visible=bool(self._user32.IsWindowVisible(hwnd)),
            is_active=True,
        )

    async def focus_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Bring target window to foreground."""
        if not self._user32:
            return ActionResult(
                success=False,
                message="Windows backend unavailable on this OS",
                error_class=ComputerFailureClass.BACKEND_UNAVAILABLE,
            )

        target_hwnd = None
        if isinstance(target, WindowInfo):
            target_hwnd = target.handle
        elif isinstance(target, int):
            target_hwnd = target
        elif isinstance(target, str):
            windows = await self.get_windows()
            q = target.lower()
            for w in windows:
                if q in w.title.lower():
                    target_hwnd = w.handle
                    break

        if not target_hwnd:
            return ActionResult(
                success=False,
                message=f"No window found matching '{target}'",
                error_class=ComputerFailureClass.WINDOW_NOT_FOUND,
            )

        try:
            self._user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
            self._user32.SetForegroundWindow(target_hwnd)
            await asyncio.sleep(0.2)
            title = self._get_window_title(target_hwnd)
            return ActionResult(
                success=True,
                message=f"Focused window: {title}",
                data={"handle": target_hwnd, "title": title},
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                message=f"Failed to focus window: {exc}",
                error_class=ComputerFailureClass.UNKNOWN_COMPUTER_ERROR,
            )

    async def launch_application(self, name: str, path: str = "") -> ActionResult:
        """Launch application using AppTool and verify window appearance."""
        try:
            from tools.app_tool import AppTool
            res = AppTool.open_application(name=name, path=path)
            if res.get("status") == "error":
                return ActionResult(
                    success=False,
                    message=res.get("message", "Application launch failed"),
                    error_class=ComputerFailureClass.APPLICATION_NOT_FOUND,
                )

            # Wait briefly for window to materialize
            await asyncio.sleep(1.0)
            windows = await self.get_windows()
            matched = None
            term = (name or path).lower()
            for w in windows:
                if term in w.title.lower() or "code" in term and "code" in w.title.lower():
                    matched = w
                    break

            return ActionResult(
                success=True,
                message=f"Launched '{name or path}' successfully",
                data={"window": matched.to_dict() if matched else None},
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                message=str(exc),
                error_class=ComputerFailureClass.UNKNOWN_COMPUTER_ERROR,
            )

    async def close_window(self, target: WindowInfo | int | str) -> ActionResult:
        """Close window via WM_CLOSE."""
        if not self._user32:
            return ActionResult(
                success=False,
                message="Windows backend unavailable",
                error_class=ComputerFailureClass.BACKEND_UNAVAILABLE,
            )

        target_hwnd = None
        if isinstance(target, WindowInfo):
            target_hwnd = target.handle
        elif isinstance(target, int):
            target_hwnd = target
        elif isinstance(target, str):
            windows = await self.get_windows()
            q = target.lower()
            for w in windows:
                if q in w.title.lower():
                    target_hwnd = w.handle
                    break

        if not target_hwnd:
            return ActionResult(
                success=False,
                message=f"Window not found: {target}",
                error_class=ComputerFailureClass.WINDOW_NOT_FOUND,
            )

        title = self._get_window_title(target_hwnd)
        self._user32.PostMessageW(target_hwnd, WM_CLOSE, 0, 0)
        return ActionResult(success=True, message=f"Closed window: {title}")

    async def get_accessibility_tree(
        self,
        window: WindowInfo | None = None,
        max_depth: int = 4,
    ) -> ElementInfo | None:
        """Query native Windows UI Automation via PowerShell/.NET."""
        target_win = window or await self.get_active_window()
        if not target_win:
            return None

        hwnd = target_win.handle
        ps_cmd = (
            "$u = Add-Type -MemberDefinition '"
            "[DllImport(\"user32.dll\")] public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);"
            "[DllImport(\"user32.dll\")] public static extern bool SetThreadDesktop(IntPtr hDesktop);"
            "' -Name 'W32DeskTree' -Namespace 'W32' -PassThru -ErrorAction SilentlyContinue; "
            "$hd = $u::OpenDesktop('default', 0, $false, 0x1FF); "
            "if ($hd) { [void]$u::SetThreadDesktop($hd); }; "
            f"$hwnd = [IntPtr]{hwnd}; "
            "Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue; "
            "Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue; "
            "$ae = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd); "
            "if (-not $ae) { Write-Output '{}'; exit }; "
            "function Dump-Elem($e, $depth) { "
            "  if ($depth -gt 3 -or -not $e) { return $null }; "
            "  $r = $e.Current.BoundingRectangle; "
            "  $pats = @($e.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName.Replace('PatternIdentifiers.Pattern', '').Replace('Pattern', '') }); "
            "  $obj = [PSCustomObject]@{ "
            "    name = $e.Current.Name; "
            "    role = $e.Current.ControlType.ProgrammaticName.Replace('ControlType.', '').ToLower(); "
            "    automation_id = $e.Current.AutomationId; "
            "    is_enabled = $e.Current.IsEnabled; "
            "    patterns = $pats; "
            "    bounds = @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom); "
            "    children = @() "
            "  }; "
            "  $c = $e.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition); "
            "  foreach ($child in $c) { "
            "    $childObj = Dump-Elem $child ($depth + 1); "
            "    if ($childObj) { $obj.children += $childObj } "
            "  }; "
            "  return $obj "
            "}; "
            "$root = Dump-Elem $ae 0; "
            "$root | ConvertTo-Json -Depth 5"
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            raw = stdout.decode("utf-8", errors="replace").strip()
            if not raw or raw == "{}":
                # Fallback to single top-level node for the window
                return ElementInfo(
                    id=f"win_{hwnd}",
                    role="window",
                    name=target_win.title,
                    bounds=target_win.rect,
                )

            brace_idx = raw.find("{")
            if brace_idx != -1:
                raw = raw[brace_idx:]

            data = json.loads(raw)

            def _parse(d: dict[str, Any], idx: str) -> ElementInfo:
                raw_b = d.get("bounds") or [0, 0, 0, 0]
                bounds = (int(raw_b[0]), int(raw_b[1]), int(raw_b[2]), int(raw_b[3])) if len(raw_b) >= 4 else (0, 0, 0, 0)
                children = []
                for i, c in enumerate(d.get("children") or []):
                    if isinstance(c, dict):
                        children.append(_parse(c, f"{idx}_{i}"))
                raw_patterns = d.get("patterns") or []
                if isinstance(raw_patterns, str):
                    raw_patterns = [raw_patterns]
                patterns = tuple(str(p) + "Pattern" if not str(p).endswith("Pattern") else str(p) for p in raw_patterns)
                return ElementInfo(
                    id=idx,
                    role=str(d.get("role") or "pane"),
                    name=str(d.get("name") or ""),
                    bounds=bounds,
                    is_enabled=bool(d.get("is_enabled", True)),
                    automation_id=d.get("automation_id"),
                    supported_patterns=patterns,
                    children=tuple(children),
                )

            return _parse(data, f"win_{hwnd}")
        except Exception as exc:
            logger.debug("Windows UIA tree query fallback: %s", exc)
            return ElementInfo(
                id=f"win_{hwnd}",
                role="window",
                name=target_win.title,
                bounds=target_win.rect,
            )

    async def invoke_pattern(
        self,
        element: ElementInfo,
        pattern_name: str,
        value: Any = None,
    ) -> ActionResult:
        """Invoke native UI Automation control pattern via PowerShell / UIAutomationClient."""
        if not self._user32:
            return ActionResult(
                success=False,
                message="Windows backend unavailable",
                error_class=ComputerFailureClass.BACKEND_UNAVAILABLE,
            )

        p_clean = pattern_name.replace("Pattern", "")
        auto_id = element.automation_id or ""
        elem_name = (element.name or "").replace('"', '`"')

        ps_cmd = (
            "$u = Add-Type -MemberDefinition '"
            "[DllImport(\"user32.dll\")] public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);"
            "[DllImport(\"user32.dll\")] public static extern bool SetThreadDesktop(IntPtr hDesktop);"
            "' -Name 'W32DeskInv' -Namespace 'W32' -PassThru -ErrorAction SilentlyContinue; "
            "$hd = $u::OpenDesktop('default', 0, $false, 0x1FF); "
            "if ($hd) { [void]$u::SetThreadDesktop($hd); }; "
            "Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue; "
            "Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue; "
            "$root = [System.Windows.Automation.AutomationElement]::RootElement; "
        )

        if auto_id:
            ps_cmd += f"$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '{auto_id}'); "
        elif elem_name:
            ps_cmd += f"$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, \"{elem_name}\"); "
        else:
            ps_cmd += "$cond = [System.Windows.Automation.Condition]::TrueCondition; "

        ps_cmd += (
            "$elem = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond); "
            "if (-not $elem) { Write-Output 'ELEMENT_NOT_FOUND'; exit }; "
        )

        p_lower = p_clean.lower()
        if p_lower == "invoke":
            ps_cmd += (
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern); "
                "  $p.Invoke(); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        elif p_lower == "value":
            val_esc = str(value or "").replace('"', '`"')
            ps_cmd += (
                f"$val = \"{val_esc}\"; "
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); "
                "  $p.SetValue($val); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        elif p_lower == "toggle":
            ps_cmd += (
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern); "
                "  $p.Toggle(); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        elif p_lower == "expandcollapse":
            ps_cmd += (
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern); "
                "  $p.Expand(); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        elif p_lower == "selectionitem":
            ps_cmd += (
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern); "
                "  $p.Select(); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        elif p_lower == "scroll":
            ps_cmd += (
                "try { "
                "  $p = $elem.GetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern); "
                "  $p.Scroll([System.Windows.Automation.ScrollAmount]::SmallIncrement, [System.Windows.Automation.ScrollAmount]::NoAmount); "
                "  Write-Output 'SUCCESS'; "
                "} catch { Write-Output ('ERROR: ' + $_.Exception.Message) }"
            )
        else:
            return ActionResult(
                success=False,
                message=f"Unsupported pattern: {pattern_name}",
                error_class=ComputerFailureClass.CONTROL_DISABLED,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            out = stdout.decode("utf-8", errors="replace").strip()
            if "SUCCESS" in out:
                return ActionResult(
                    success=True,
                    message=f"Invoked {pattern_name} on '{element.name}' ({element.role})",
                    data={"pattern": pattern_name, "element_id": element.id, "method": "uia_pattern"},
                )
            if "ELEMENT_NOT_FOUND" in out:
                return ActionResult(
                    success=False,
                    message=f"Element '{element.name}' not found for pattern invocation",
                    error_class=ComputerFailureClass.CONTROL_NOT_FOUND,
                )
            return ActionResult(
                success=False,
                message=f"Pattern invocation error: {out}",
                error_class=ComputerFailureClass.CONTROL_DISABLED,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                message=f"Failed to invoke pattern: {exc}",
                error_class=ComputerFailureClass.UNKNOWN_COMPUTER_ERROR,
            )

    async def click_element(
        self,
        element: ElementInfo | None = None,
        coords: tuple[int, int] | None = None,
    ) -> ActionResult:
        """Click an element center or specified coordinates.

        Attempts structured UI Automation pattern first before mouse coordinate fallback.
        """
        if not self._user32:
            return ActionResult(success=False, message="Windows backend unavailable", error_class=ComputerFailureClass.BACKEND_UNAVAILABLE)

        # Hierarchy Level 4: Windows UI Automation pattern invocation
        if element and element.supported_patterns:
            clean_patterns = [p.replace("Pattern", "").lower() for p in element.supported_patterns]
            if "invoke" in clean_patterns or element.role in ("button", "menuitem", "splitbutton", "hyperlink"):
                res = await self.invoke_pattern(element, "InvokePattern")
                if res.success:
                    return res
            elif "toggle" in clean_patterns or element.role in ("checkbox", "radiobutton"):
                res = await self.invoke_pattern(element, "TogglePattern")
                if res.success:
                    return res
            elif "selectionitem" in clean_patterns or element.role in ("tabitem", "listitem"):
                res = await self.invoke_pattern(element, "SelectionItemPattern")
                if res.success:
                    return res
            elif "expandcollapse" in clean_patterns or element.role in ("combobox", "treeitem"):
                res = await self.invoke_pattern(element, "ExpandCollapsePattern")
                if res.success:
                    return res

        # Hierarchy Level 6: Mouse coordinate fallback
        x, y = 0, 0
        target_desc = ""
        if element:
            l, t, r, b = element.bounds
            x = (l + r) // 2
            y = (t + b) // 2
            target_desc = f"'{element.name}' ({element.role})"
        elif coords:
            x, y = coords
            target_desc = f"({x}, {y})"
        else:
            return ActionResult(
                success=False,
                message="Neither element nor coordinates provided",
                error_class=ComputerFailureClass.CONTROL_NOT_FOUND,
            )

        self._attach_thread_desktop()
        self._user32.SetCursorPos(x, y)
        await asyncio.sleep(0.05)
        self._user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        await asyncio.sleep(0.05)
        self._user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        return ActionResult(
            success=True,
            message=f"Clicked {target_desc} at ({x}, {y})",
            data={"x": x, "y": y, "method": "coordinate_fallback"},
        )

    async def type_text(self, text: str, clear_first: bool = False) -> ActionResult:
        """Type characters sequentially via keybd_event."""
        if not self._user32:
            return ActionResult(success=False, message="Windows backend unavailable", error_class=ComputerFailureClass.BACKEND_UNAVAILABLE)

        if clear_first:
            # Select all + Backspace
            await self.hotkey(["ctrl", "a"])
            await self.press_key("backspace")

        import ctypes
        for char in text:
            # VkKeyScanW maps unicode character to virtual key + shift state
            vk_res = self._user32.VkKeyScanW(ord(char))
            if vk_res == -1:
                continue
            vk = vk_res & 0xFF
            shift = (vk_res >> 8) & 1

            if shift:
                self._user32.keybd_event(VK_MAP["shift"], 0, 0, 0)
            self._user32.keybd_event(vk, 0, 0, 0)
            self._user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            if shift:
                self._user32.keybd_event(VK_MAP["shift"], 0, KEYEVENTF_KEYUP, 0)
            await asyncio.sleep(0.01)

        return ActionResult(success=True, message=f"Typed {len(text)} characters")

    async def press_key(self, key: str) -> ActionResult:
        """Press single key."""
        if not self._user32:
            return ActionResult(success=False, message="Windows backend unavailable", error_class=ComputerFailureClass.BACKEND_UNAVAILABLE)

        k_norm = (key or "").lower().strip()
        vk = VK_MAP.get(k_norm)
        if not vk and len(k_norm) == 1:
            vk = ord(k_norm.upper())

        if not vk:
            return ActionResult(success=False, message=f"Unknown key: {key}", error_class=ComputerFailureClass.UNKNOWN_COMPUTER_ERROR)

        self._user32.keybd_event(vk, 0, 0, 0)
        await asyncio.sleep(0.02)
        self._user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return ActionResult(success=True, message=f"Pressed key: {key}")

    async def hotkey(self, keys: list[str]) -> ActionResult:
        """Press combination of keys simultaneously."""
        if not self._user32:
            return ActionResult(success=False, message="Windows backend unavailable", error_class=ComputerFailureClass.BACKEND_UNAVAILABLE)

        vks: list[int] = []
        for k in keys:
            k_norm = k.lower().strip()
            vk = VK_MAP.get(k_norm)
            if not vk and len(k_norm) == 1:
                vk = ord(k_norm.upper())
            if vk:
                vks.append(vk)

        if not vks:
            return ActionResult(success=False, message=f"Invalid hotkey: {keys}", error_class=ComputerFailureClass.UNKNOWN_COMPUTER_ERROR)

        for vk in vks:
            self._user32.keybd_event(vk, 0, 0, 0)
        await asyncio.sleep(0.05)
        for vk in reversed(vks):
            self._user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

        return ActionResult(success=True, message=f"Dispatched hotkey: {'+'.join(keys)}")

    async def move_mouse(self, x: int, y: int) -> ActionResult:
        if not self._user32:
            return ActionResult(success=False, message="Windows backend unavailable", error_class=ComputerFailureClass.BACKEND_UNAVAILABLE)
        self._user32.SetCursorPos(x, y)
        return ActionResult(success=True, message=f"Moved mouse to ({x}, {y})")

    async def take_screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bytes | None, int, int]:
        """Capture screenshot via PIL.ImageGrab with graceful non-interactive fallback."""
        try:
            self._attach_thread_desktop()
            from PIL import ImageGrab
            bbox = region if region else None
            img = ImageGrab.grab(bbox=bbox)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), img.width, img.height
        except Exception as exc:
            logger.debug("Live screenshot unavailable (non-interactive display session): %s", exc)
            return None, 1920, 1080
