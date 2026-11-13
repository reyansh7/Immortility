"""Live Windows Desktop Smoke Tests (Executed only when explicitly requested).

Run with:
    pytest -m windows_live tests/test_windows_live_smoke.py
"""

import os
import pytest

from core.computer.controller import ComputerController
from core.computer.failsafe import EmergencyStop
from core.computer.windows_backend import WindowsDesktopBackend
from tools.computer_tool import ComputerTool


@pytest.fixture(autouse=True)
def reset_failsafe():
    stop = EmergencyStop()
    stop.reset()
    yield
    stop.reset()


@pytest.fixture
def live_controller():
    if os.name != "nt":
        pytest.skip("Live Windows smoke tests require Windows (os.name == 'nt')")
    backend = WindowsDesktopBackend()
    ctrl = ComputerController(backend=backend)
    ComputerController.set_instance(ctrl)
    yield ctrl
    ComputerController.set_instance(None)


@pytest.mark.windows_live
class TestLiveWindowsSmoke:
    @pytest.mark.asyncio
    async def test_live_window_discovery_and_active(self, live_controller):
        """Live Check 1 & 2: computer_list_windows and computer_get_active_window."""
        windows = await live_controller.get_windows()
        assert len(windows) >= 1, "Expected at least 1 top-level window on live desktop"
        for w in windows[:3]:
            assert w.handle > 0
            assert len(w.title) > 0

        active = await live_controller.get_active_window()
        # Foreground window may be None if desktop locked or during transitions, but if present must be valid
        if active:
            assert active.handle > 0
            assert active.is_active

    @pytest.mark.asyncio
    async def test_live_screenshot(self, live_controller):
        """Live Check 3: computer_screenshot captures valid screen dimensions."""
        obs = await live_controller.observe_screen(force_fresh=True)
        assert obs.width > 0
        assert obs.height > 0
        assert len(obs.screen_hash) > 0

    @pytest.mark.asyncio
    async def test_live_vscode_or_desktop_uia_patterns(self, live_controller):
        """Live Checks 4, 5, 6, 7, 8, 9:

        Detect VS Code (or fallback to active window), inspect UIA tree, and verify patterns.
        """
        windows = await live_controller.get_windows()
        vscode_win = next((w for w in windows if "visual studio code" in w.title.lower()), None)
        target_win = (
            vscode_win
            or next((w for w in windows if any(app in w.title.lower() for app in ("chrome", "brave", "whatsapp", "antigravity", "edge", "notepad"))), None)
            or (await live_controller.get_active_window())
        )
        if not target_win:
            pytest.skip("LIVE_WINDOWS_TEST_SKIPPED: No active or target desktop window available")

        # Focus window
        focus_res = await live_controller.focus_window(target_win.handle)
        assert focus_res.success, f"Failed to focus window: {target_win.title}"

        # Retrieve UI Automation tree
        tree = await live_controller.get_accessibility_tree(window_title=target_win.title, max_depth=3)
        assert "controls" in tree
        controls = tree.get("controls", [])
        assert len(controls) >= 1, f"Expected at least one control in {target_win.title}"

        # Check for supported UIA patterns
        controls_with_patterns = [c for c in controls if c.get("supported_patterns")]
        assert len(controls_with_patterns) >= 1, "Expected UI elements with supported control patterns"

        actionable = controls_with_patterns[0]
        assert "role" in actionable
        assert "name" in actionable
        assert "supported_patterns" in actionable

    @pytest.mark.asyncio
    async def test_live_emergency_stop_cycle(self, live_controller):
        """Live Checks 12, 13, 14, 15: EmergencyStop blocking and manual reset."""
        failsafe = EmergencyStop()
        failsafe.trigger("Live Windows Smoke emergency test")
        assert failsafe.is_stopped

        # Subsequent mutation blocked
        with pytest.raises(PermissionError) as exc_info:
            await live_controller.click(name="Test", role="button")
        assert "EMERGENCY STOP" in str(exc_info.value)

        # Reset and verify recovery
        failsafe.reset()
        assert not failsafe.is_stopped
