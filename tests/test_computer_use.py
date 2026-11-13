"""Unit and subsystem tests for IMMORTALITY Computer Use."""

import pytest
from core.computer.contracts import (
    ActionResult,
    ComputerFailureClass,
    ElementInfo,
    WindowInfo,
)
from core.computer.controller import ComputerController
from core.computer.failsafe import EmergencyStop
from core.computer.mock_backend import MockDesktopBackend
from core.computer.observer import ScreenObserver
from core.computer.tree_filter import AccessibilityTreeFilter
from core.permissions import (
    MODE_ASSISTED,
    MODE_AUTONOMOUS,
    MODE_SAFE,
    decide,
)
from core.world_model import WorldModel
from tools.computer_tool import ComputerTool
from tools.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def reset_failsafe():
    stop = EmergencyStop()
    stop.reset()
    yield
    stop.reset()


@pytest.fixture
def mock_backend():
    return MockDesktopBackend()


@pytest.fixture
def controller(mock_backend):
    ctrl = ComputerController(backend=mock_backend)
    ComputerController.set_instance(ctrl)
    return ctrl


class TestWindowDiscoveryAndFocus:
    @pytest.mark.asyncio
    async def test_window_enumeration(self, controller):
        windows = await controller.get_windows()
        assert len(windows) >= 3
        titles = [w.title for w in windows]
        assert any("Visual Studio Code" in t for t in titles)
        assert any("Chrome" in t for t in titles)

    @pytest.mark.asyncio
    async def test_active_window_detection(self, controller):
        active = await controller.get_active_window()
        assert active is not None
        assert "Visual Studio Code" in active.title
        assert active.is_active

    @pytest.mark.asyncio
    async def test_focus_window_by_title(self, controller):
        res = await controller.focus_window("Chrome")
        assert res.success
        assert "Chrome" in res.message

        active = await controller.get_active_window()
        assert active is not None
        assert "Chrome" in active.title

    @pytest.mark.asyncio
    async def test_focus_window_not_found(self, controller):
        res = await controller.focus_window("NonExistentWindow12345")
        assert not res.success
        assert res.error_class == ComputerFailureClass.WINDOW_NOT_FOUND

    @pytest.mark.asyncio
    async def test_close_window(self, controller):
        res = await controller.close_window("PowerShell")
        assert res.success
        windows = await controller.get_windows()
        assert not any("PowerShell" in w.title for w in windows)


class TestAccessibilityTreeAndFiltering:
    def test_tree_filtering_and_compacting(self):
        btn = ElementInfo(id="b1", role="button", name="Submit", bounds=(10, 10, 50, 30))
        inp = ElementInfo(id="i1", role="edit", name="Username", bounds=(10, 40, 100, 60), value="admin")
        zero_size = ElementInfo(id="z1", role="pane", name="Hidden", bounds=(0, 0, 0, 0))
        container = ElementInfo(
            id="c1",
            role="window",
            name="Login Dialog",
            bounds=(0, 0, 500, 400),
            children=(btn, inp, zero_size),
        )

        filterer = AccessibilityTreeFilter(max_controls=10)
        filtered = filterer.filter_tree(container)

        assert filtered["root_name"] == "Login Dialog"
        controls = filtered["controls"]
        assert len(controls) == 2  # btn and inp kept; zero_size dropped
        names = [c["name"] for c in controls]
        assert "Submit" in names
        assert "Username" in names

    def test_element_grounding_by_role_and_name(self):
        btn_run = ElementInfo(id="b_run", role="button", name="Run", bounds=(10, 10, 60, 30))
        btn_stop = ElementInfo(id="b_stop", role="button", name="Stop", bounds=(70, 10, 120, 30))
        root = ElementInfo(
            id="r",
            role="window",
            name="Main Window",
            bounds=(0, 0, 800, 600),
            children=(btn_run, btn_stop),
        )

        filterer = AccessibilityTreeFilter()
        match = filterer.find_element(root, name="Run", role="button")
        assert match is not None
        assert match.id == "b_run"

        no_match = filterer.find_element(root, name="Debug", role="button")
        assert no_match is None


class TestScreenObserverAndHashing:
    @pytest.mark.asyncio
    async def test_screen_hash_and_cache(self, mock_backend):
        observer = ScreenObserver(mock_backend, cache_ttl_s=10.0)

        # First observation
        obs1 = await observer.observe_screen()
        assert obs1.screenshot_bytes is not None
        assert len(obs1.screen_hash) > 0
        v1 = obs1.perception_version

        # Second observation without state change reuses cache
        obs2 = await observer.observe_screen()
        assert obs2.screen_hash == obs1.screen_hash
        assert obs2.perception_version == v1

        # Invalidate cache forces new perception version
        observer.invalidate_cache()
        obs3 = await observer.observe_screen(force_fresh=True)
        assert obs3.perception_version > v1


class TestActionsAndGrounding:
    @pytest.mark.asyncio
    async def test_click_grounded_element(self, controller, mock_backend):
        # Click Run button in VS Code tree
        res = await controller.click(name="Run", role="button")
        assert res.success
        assert any("Run" in c for c in mock_backend.clicked_history)

    @pytest.mark.asyncio
    async def test_click_missing_element_fails(self, controller):
        res = await controller.click(name="MissingButton", role="button")
        assert not res.success
        assert res.error_class == ComputerFailureClass.CONTROL_NOT_FOUND

    @pytest.mark.asyncio
    async def test_click_coordinates_fallback(self, controller, mock_backend):
        res = await controller.click(coords=(450, 320))
        assert res.success
        assert "coords:450,320" in mock_backend.clicked_history

    @pytest.mark.asyncio
    async def test_type_and_hotkey(self, controller, mock_backend):
        await controller.type_text("npm test\n")
        assert "npm test\n" in mock_backend.typed_history

        await controller.hotkey(["ctrl", "shift", "p"])
        assert ["ctrl", "shift", "p"] in mock_backend.hotkey_history


class TestEmergencyStop:
    @pytest.mark.asyncio
    async def test_emergency_stop_blocks_mutations(self, controller):
        failsafe = EmergencyStop()
        assert not failsafe.is_stopped

        # Trigger emergency stop
        failsafe.trigger("User pressed Emergency Stop hotkey")
        assert failsafe.is_stopped

        # Mutating action raises PermissionError
        with pytest.raises(PermissionError) as exc_info:
            await controller.click(name="Run", role="button")
        assert "EMERGENCY STOP" in str(exc_info.value)

        # Mutating launch raises PermissionError
        with pytest.raises(PermissionError):
            await controller.launch_application("notepad")

        # Reset clears block
        failsafe.reset()
        assert not failsafe.is_stopped
        res = await controller.click(name="Run", role="button")
        assert res.success


class TestPermissionBoundaryForComputerUse:
    def test_read_only_computer_tools_always_allowed(self):
        for tool in ("computer_list_windows", "computer_get_active_window", "computer_get_accessibility_tree", "computer_screenshot"):
            dec_safe = decide(tool, mode=MODE_SAFE)
            assert dec_safe.allowed, f"{tool} should be allowed in SAFE mode"

            dec_assist = decide(tool, mode=MODE_ASSISTED)
            assert dec_assist.allowed, f"{tool} should be allowed in ASSISTED mode"

    def test_mutating_computer_tools_require_confirm_in_assisted(self):
        for tool in ("computer_launch_app", "computer_focus_window", "computer_close_window", "computer_click", "computer_type", "computer_press_key", "computer_hotkey"):
            dec_assist = decide(tool, mode=MODE_ASSISTED)
            assert dec_assist.needs_confirm, f"{tool} should require confirmation in ASSISTED mode"

            dec_safe = decide(tool, mode=MODE_SAFE)
            assert dec_safe.denied, f"{tool} should be denied in SAFE mode"

            dec_auto = decide(tool, mode=MODE_AUTONOMOUS)
            assert dec_auto.allowed, f"{tool} should be allowed in AUTONOMOUS mode"


class TestWorldModelComputerIntegration:
    @pytest.mark.asyncio
    async def test_world_model_updates_on_window_focus(self, controller):
        model = WorldModel.probe()
        assert model.active_window == ""

        # Launching or focusing an application emits computer.window_changed
        await controller.focus_window("Chrome")

        assert "Chrome" in model.active_window
        assert "Google Chrome" in model.active_application

        summary = model.get_summary()
        assert "Active Window: Google Chrome" in summary or "Chrome" in summary


class TestUIAutomationPatterns:
    @pytest.mark.asyncio
    async def test_button_invokes_pattern_before_coordinates(self, controller, mock_backend):
        """Phase 1D: Buttons use InvokePattern rather than coordinate clicking."""
        res = await controller.click(name="Run", role="button")
        assert res.success
        assert res.data.get("pattern") == "InvokePattern"
        assert any(p[1] == "InvokePattern" for p in mock_backend.patterns_invoked)
        # Coordinate click was not recorded because UIA InvokePattern succeeded
        assert not any("coords:" in c for c in mock_backend.clicked_history)

    @pytest.mark.asyncio
    async def test_toggle_uses_toggle_pattern(self, controller, mock_backend):
        """Phase 1D: Checkbox / toggle uses TogglePattern."""
        res = await controller.click(name="Word Wrap", role="checkbox")
        assert res.success
        assert res.data.get("pattern") == "TogglePattern"
        assert any(p[1] == "TogglePattern" for p in mock_backend.patterns_invoked)

    @pytest.mark.asyncio
    async def test_tab_uses_selection_pattern(self, controller, mock_backend):
        """Phase 1D: TabItem uses SelectionItemPattern."""
        res = await controller.click(name="Terminal", role="tabitem")
        assert res.success
        assert res.data.get("pattern") == "SelectionItemPattern"
        assert any(p[1] == "SelectionItemPattern" for p in mock_backend.patterns_invoked)

    @pytest.mark.asyncio
    async def test_type_uses_value_pattern_on_edit(self, controller, mock_backend):
        """Phase 1D: Target edit field uses ValuePattern."""
        res = await controller.type_text("Search query", name="Search")
        assert res.success
        assert any(p[1] == "ValuePattern" and p[2] == "Search query" for p in mock_backend.patterns_invoked)
        assert "Search query" in mock_backend.typed_history

    @pytest.mark.asyncio
    async def test_pattern_failure_falls_back_to_coordinates(self, controller, mock_backend):
        """Phase 1C: When UIA pattern fails, fall back safely to coordinates."""
        mock_backend.fail_pattern["InvokePattern"] = ComputerFailureClass.CONTROL_DISABLED
        res = await controller.click(name="Run", role="button")
        assert res.success
        # Coordinate click fallback was invoked
        assert any("coords:" in c for c in mock_backend.clicked_history)


class TestStaleElementProtection:
    @pytest.mark.asyncio
    async def test_stale_element_detected_and_regrounded(self, controller, mock_backend):
        """Phase 1E: Outdated element reference detected, re-grounded, and retried."""
        active_win = await mock_backend.get_active_window()
        assert active_win is not None
        tree1 = await mock_backend.get_accessibility_tree(active_win)
        assert tree1 is not None

        filterer = AccessibilityTreeFilter()
        initial_elem = filterer.find_element(tree1, name="Run", role="button")
        assert initial_elem is not None
        assert initial_elem.generation_id == 1

        # Simulate UI change: update tree to generation 2
        new_btn = ElementInfo(
            id="vscode_btn_run_v2",
            role="button",
            name="Run",
            bounds=(310, 70, 370, 100),
            automation_id="workbench.action.debug.start",
            supported_patterns=("InvokePattern",),
            generation_id=2,
        )
        new_root = ElementInfo(
            id="vscode_root_v2",
            role="window",
            name="Visual Studio Code - Forge",
            bounds=(50, 50, 1800, 1000),
            generation_id=2,
            children=(new_btn,),
        )
        mock_backend.update_tree(active_win.handle, new_root)

        # Passing initial_elem (gen 1) triggers stale detection and re-grounding
        res = await controller.click(element=initial_elem)
        assert res.success
        assert res.data.get("pattern") == "InvokePattern"

    @pytest.mark.asyncio
    async def test_stale_element_unrecoverable_fails_cleanly(self, controller, mock_backend):
        """Phase 1E: Missing element after tree refresh returns STALE_ELEMENT."""
        active_win = await mock_backend.get_active_window()
        assert active_win is not None
        old_elem = ElementInfo(
            id="old_nonexistent_btn",
            role="button",
            name="OldButton",
            bounds=(10, 10, 50, 50),
            generation_id=0,
        )

        res = await controller.click(element=old_elem)
        assert not res.success
        assert res.error_class == ComputerFailureClass.STALE_ELEMENT


class TestEmergencyStopComprehensive:
    @pytest.mark.asyncio
    async def test_stop_before_mutation(self, controller):
        """1. Stop before mutation blocks operation."""
        EmergencyStop().trigger("Emergency stop triggered before action")
        with pytest.raises(PermissionError) as exc_info:
            await controller.click(name="Run", role="button")
        assert "EMERGENCY STOP" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_stop_between_mutations(self, controller):
        """2. Stop between two mutations blocks second mutation."""
        res1 = await controller.click(name="Run", role="button")
        assert res1.success

        EmergencyStop().trigger("User pressed Emergency hotkey between steps")
        with pytest.raises(PermissionError):
            await controller.click(name="Word Wrap", role="checkbox")

    @pytest.mark.asyncio
    async def test_stop_blocks_subsequent_mutations(self, controller):
        """4. Stop blocks next mutation."""
        EmergencyStop().trigger("Emergency stop active")
        with pytest.raises(PermissionError):
            await controller.type_text("malicious payload")
        with pytest.raises(PermissionError):
            await controller.hotkey(["ctrl", "c"])
        with pytest.raises(PermissionError):
            await controller.launch_application("cmd.exe")

    @pytest.mark.asyncio
    async def test_read_only_allowed_during_stop(self, controller):
        """5. Read-only operations behave according to intended policy during emergency stop."""
        EmergencyStop().trigger("Diagnostic stop")
        # Read-only observation tools remain safe and functional
        windows = await controller.get_windows()
        assert len(windows) >= 1
        active = await controller.get_active_window()
        assert active is not None
        obs = await controller.observe_screen()
        assert len(obs.screen_hash) > 0
        tree = await controller.get_accessibility_tree()
        assert "controls" in tree

    def test_llm_cannot_reset_emergency_stop(self):
        """6. Verify tool registry has NO tool to reset emergency stop."""
        registry = ToolRegistry()
        assert "emergency_stop_reset" not in registry._tools
        assert "computer_reset_emergency_stop" not in registry._tools
        assert "reset_emergency_stop" not in registry._tools
        assert not any("reset_emergency" in name for name in registry._tools)

    @pytest.mark.asyncio
    async def test_explicit_reset_restores_operation(self, controller):
        """7. Explicit local reset restores mutation capabilities."""
        EmergencyStop().trigger("Temporary halt")
        assert EmergencyStop().is_stopped
        with pytest.raises(PermissionError):
            await controller.click(name="Run", role="button")

        # Explicit local reset
        EmergencyStop().reset()
        assert not EmergencyStop().is_stopped
        res = await controller.click(name="Run", role="button")
        assert res.success

    def test_resumed_task_preserves_permission_boundary(self):
        """8. Resumed task still strictly enforces permission boundary."""
        EmergencyStop().trigger("Stop")
        EmergencyStop().reset()
        # Even after reset, mutating actions still require confirmation in assisted mode
        for tool in ("computer_click", "computer_type", "computer_launch_app"):
            dec = decide(tool, mode=MODE_ASSISTED)
            assert dec.needs_confirm, f"{tool} must still require confirmation in assisted mode"

