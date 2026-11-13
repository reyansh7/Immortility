"""IMMORTALITY Computer Use Subsystem."""

from core.computer.contracts import (
    ActionResult,
    ComputerBackend,
    ComputerFailureClass,
    ElementInfo,
    ScreenObservation,
    VisionProvider,
    WindowInfo,
)
from core.computer.controller import ComputerController
from core.computer.failsafe import EmergencyStop
from core.computer.mock_backend import MockDesktopBackend
from core.computer.observer import ScreenObserver
from core.computer.tree_filter import AccessibilityTreeFilter
from core.computer.windows_backend import WindowsDesktopBackend

__all__ = [
    "ActionResult",
    "ComputerBackend",
    "ComputerController",
    "ComputerFailureClass",
    "ElementInfo",
    "EmergencyStop",
    "MockDesktopBackend",
    "ScreenObservation",
    "ScreenObserver",
    "AccessibilityTreeFilter",
    "VisionProvider",
    "WindowsDesktopBackend",
    "WindowInfo",
]
