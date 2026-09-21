# 07 — Computer Use

## The JARVIS Capability Gap

The most visible gap between Immortality today and a true JARVIS is computer control. Immortality can read and write files, run commands, and operate the browser. It cannot:

- Take a screenshot and understand what is on screen
- Find and click a button in VS Code
- Open an application
- Read text from a native app dialog
- Type into any app that is not a browser
- Observe what the user is currently looking at

These capabilities are required for JARVIS to "handle" a task that involves native Windows applications.

---

## Control Hierarchy

Computer control should use the most structured, reliable interface available. Falling back to less structured interfaces only when necessary.

```
LEVEL 1 — PROGRAMMATIC APIs (always prefer)
    git_*, file_*, run_command, db_query
    Direct function calls — zero ambiguity, structured results

LEVEL 2 — BROWSER DOM (for web applications)
    Playwright + accessibility tree
    Role-based element selection — no coordinates
    Already implemented in tools/browser_agent.py

LEVEL 3 — WINDOWS UI AUTOMATION (for native apps)
    pywinauto + UIA COM API
    Accessibility tree for Windows apps (VS Code, Notepad, Explorer)
    Element selection by role + name — no coordinates
    [TO BUILD — Phase 2]

LEVEL 4 — VISION-BASED CONTROL (when UIA fails)
    Screenshot → vision model → element description → action
    Used when an application exposes no accessibility tree
    Slower and less reliable than Level 3
    [TO BUILD — Phase 3]

LEVEL 5 — MOUSE/KEYBOARD SIMULATION (last resort)
    ctypes/pyautogui coordinate-based simulation
    Fragile across resolutions, DPI settings, window positions
    Only when Level 4 fails or coordinates are known precisely
```

---

## Computer Tool Design

### `tools/computer_tool.py` (to create in Phase 2)

```python
class ComputerTool:
    """
    Windows computer control via accessibility APIs.
    All methods respect the permission layer before execution.
    """

    # Observation (LOW risk — always available)
    def screenshot(self, region: dict | None = None) -> bytes
        """Capture full screen or a region as PNG bytes."""

    def list_windows(self) -> list[WindowInfo]
        """Enumerate all visible top-level windows with title, pid, rect."""

    def find_window(self, title_pattern: str) -> WindowInfo | None
        """Find a window by title (regex match)."""

    def get_focused_window(self) -> WindowInfo | None
        """Return the currently active window."""

    def get_clipboard(self) -> str
        """Read current clipboard text content."""

    # Application control (MEDIUM risk — confirm in ASSISTED)
    def launch_app(self, name_or_path: str) -> AppInfo
        """Launch an application by name or executable path."""

    def close_window(self, window: WindowInfo) -> ActionResult
        """Close a window (sends WM_CLOSE, confirms before force-kill)."""

    def set_clipboard(self, text: str) -> ActionResult
        """Write text to clipboard."""

    # Element interaction (MEDIUM risk — confirm in ASSISTED)
    def find_element(
        self,
        window: WindowInfo,
        role: str,
        name: str | None = None
    ) -> ElementInfo | None
        """Find a UI element by accessibility role and name."""

    def click_element(self, element: ElementInfo) -> ActionResult
        """Click a UI element via accessibility invoke pattern."""

    def type_text(self, element: ElementInfo, text: str) -> ActionResult
        """Type text into a focused element."""

    def press_key(self, key: str) -> ActionResult
        """Press a keyboard shortcut (e.g. 'ctrl+s', 'Enter', 'F5')."""

    def scroll_element(
        self,
        element: ElementInfo,
        direction: str,
        amount: int = 3
    ) -> ActionResult
        """Scroll an element up/down/left/right."""
```

### `WindowInfo` and `ElementInfo` structures

```python
@dataclass
class WindowInfo:
    handle: int          # Windows HWND
    title: str
    pid: int
    class_name: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    is_visible: bool

@dataclass
class ElementInfo:
    automation_id: str | None
    name: str | None
    role: str            # button, edit, list, etc.
    rect: tuple[int, int, int, int]
    is_enabled: bool
    is_focused: bool
    value: str | None    # current text/value if applicable

@dataclass
class ActionResult:
    success: bool
    message: str
    screenshot_after: bytes | None = None  # taken after action for verification
```

---

## Screen Tool Design

### `tools/screen_tool.py` (to create in Phase 3)

```python
class ScreenTool:
    """
    Screen understanding via vision model.
    Used when structural accessibility APIs are insufficient.
    """

    def analyze(
        self,
        prompt: str,
        region: dict | None = None
    ) -> str
        """
        Take a screenshot and ask the vision model about it.
        Example: analyze("What application is in focus?")
        Example: analyze("Is there an error dialog visible?")
        """

    def find_element_by_description(
        self,
        description: str
    ) -> ElementLocation | None
        """
        Take a screenshot and ask the vision model to locate
        a described UI element.
        Returns: coordinates, description, confidence
        """

    def read_text(self, region: dict | None = None) -> str
        """
        Extract visible text from the screen using OCR.
        For when text is rendered as an image (e.g., error dialogs).
        """

    def describe_screen(self) -> str
        """
        Take a screenshot and return a natural language description
        of what is visible. Used for world-state updates.
        """
```

---

## Permission Model for Computer Control

Computer control actions are stratified by risk:

```python
# Addition to core/permissions.py and core/pending_action.py

COMPUTER_CONTROL_READ = frozenset({
    "screenshot",
    "list_windows",
    "find_window",
    "get_focused_window",
    "find_element",
    "screen_analyze",
    "screen_read_text",
    "get_clipboard",
})

COMPUTER_CONTROL_MEDIUM = frozenset({
    "click_element",
    "type_text",
    "press_key",
    "scroll_element",
    "launch_app",
    "set_clipboard",
    "close_window",      # with save prompt
})

COMPUTER_CONTROL_HIGH = frozenset({
    "kill_process",      # already in system_tool.py
    "close_window_force", # without save — may lose data
    "type_password",     # typing into password fields
})
```

### Mode Behavior
| Action | SAFE | ASSISTED | AUTONOMOUS | DEVELOPER |
|---|---|---|---|---|
| screenshot | ALLOW | ALLOW | ALLOW | ALLOW |
| list_windows | ALLOW | ALLOW | ALLOW | ALLOW |
| click_element | DENY | CONFIRM | ALLOW (known) | ALLOW |
| launch_app | DENY | CONFIRM | CONFIRM | ALLOW |
| type_text | DENY | CONFIRM | CONFIRM (non-sensitive) | ALLOW |
| kill_process | DENY | CONFIRM | CONFIRM | CONFIRM |

---

## Implementation Notes

### Why pywinauto instead of PyAutoGUI

| Property | pywinauto | PyAutoGUI |
|---|---|---|
| Element targeting | By accessibility role + name | By pixel coordinates |
| Resolution independence | Yes | No |
| DPI awareness | Yes | Fragile |
| Reliability across window positions | High | Low |
| Requires screen to be visible | No (headless works) | Yes |
| Windows native app support | Excellent | Basic |
| Python package | `pip install pywinauto` | `pip install pyautogui` |

### Why not OpenCV for UI element detection

OpenCV template matching is fragile:
- Breaks across DPI settings and Windows scaling
- Requires capturing template images for each UI state
- Much slower than accessibility API lookup
- Not necessary when pywinauto covers the use case

Use OpenCV only for image analysis tasks (not UI automation).

### Screen Capture

```python
# Recommended: mss (fast, multi-monitor support)
import mss
with mss.mss() as sct:
    screenshot = sct.shot(mon=1)  # returns filename

# Alternative: PIL.ImageGrab (simpler, single monitor)
from PIL import ImageGrab
screenshot = ImageGrab.grab()
```

### Vision Model Integration

When a visual task requires the vision model:
1. Model manager evicts the current heavy model from VRAM
2. Vision model loads
3. Screenshot is encoded as base64 and sent with the prompt
4. Response is parsed
5. (Optional) Vision model is unloaded, brain reloads

This is slow (10-30 seconds per call) but correct. Use vision only when structural APIs fail.

---

## Safety Boundaries

The following computer control actions are permanently restricted:

1. **No typing into fields detected as password inputs** — use clipboard instead, warn user
2. **No sending email without CONFIRM regardless of mode** — email is irreversible
3. **No clicking on "delete", "format", "uninstall" buttons** — require explicit CONFIRM with action description
4. **No screen recording** — take individual screenshots only
5. **No keylogging** — type_text only sends to the explicitly targeted element
6. **No input injection at system level** — all input goes through the accessibility API, not raw input injection

---

## Computer Agent Role

When computer control tools are available, the `ComputerAgent` role in the agent loop uses them with a specialized prompt:

```
Role: ComputerAgent
You control the user's Windows desktop via accessibility APIs.
Before every action:
1. Take a screenshot to verify current state
2. Find the target element by role and name (not coordinates)
3. Verify element is enabled and visible
4. Execute the action
5. Take a screenshot to verify the result

Available tools: screenshot, list_windows, find_window, find_element,
click_element, type_text, press_key, launch_app, screen_analyze

Never guess at element positions. Always use find_element.
Always verify after clicking with a screenshot.
```
