"""Windows system controls — brightness & volume (no LLM PowerShell required)."""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def _key(vk: int, times: int = 1) -> None:
    user32 = ctypes.windll.user32
    for _ in range(max(1, times)):
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def _parse_percent(text: str, default: int | None = None) -> int | None:
    m = re.search(r"(\d{1,3})\s*%?", text)
    if not m:
        return default
    return max(0, min(100, int(m.group(1))))


class SystemControlTool:
    """Brighten/dim display and change system volume on Windows."""

    @staticmethod
    def set_brightness(level: int | str = 50) -> dict[str, Any]:
        try:
            pct = int(level)
        except (TypeError, ValueError):
            return {"status": "error", "message": f"Invalid brightness: {level}"}
        pct = max(0, min(100, pct))
        ps = (
            f"$b={pct}; "
            "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
            "-ErrorAction SilentlyContinue; "
            "if (-not $m) { throw 'Brightness control unavailable on this display' }; "
            "$m | ForEach-Object { $_.WmiSetBrightness(1, $b) }; "
            "Write-Output $b"
        )
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    ps,
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "failed").strip()
                return {"status": "error", "message": err[:400]}
            return {
                "status": "success",
                "message": f"Screen brightness set to {pct}%.",
                "level": pct,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def adjust_brightness(direction: str = "down", steps: int | str = 1) -> dict[str, Any]:
        """Lower/raise brightness in ~10% steps (reads current when possible)."""
        try:
            n = max(1, min(5, int(steps)))
        except (TypeError, ValueError):
            n = 1
        low = (direction or "down").lower()
        delta = -10 * n if low in {"down", "lower", "decrease", "dim"} else 10 * n

        current = SystemControlTool.get_brightness()
        cur = current.get("level")
        if isinstance(cur, int):
            target = max(0, min(100, cur + delta))
        else:
            # Fallback targets when we can't read current
            target = 30 if delta < 0 else 70
        return SystemControlTool.set_brightness(target)

    @staticmethod
    def get_brightness() -> dict[str, Any]:
        ps = (
            "$i=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
            "-ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($i) { $i.CurrentBrightness } else { '' }"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            raw = (proc.stdout or "").strip()
            if raw.isdigit():
                return {"status": "success", "level": int(raw)}
            return {"status": "error", "message": "Could not read brightness"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @staticmethod
    def set_volume(action: str = "down", steps: int | str = 5) -> dict[str, Any]:
        """action: up | down | mute | unmute. steps = key presses for up/down."""
        act = (action or "down").lower().strip()
        try:
            n = max(1, min(50, int(steps)))
        except (TypeError, ValueError):
            n = 5

        try:
            if act in {"mute", "unmute", "toggle"}:
                _key(VK_VOLUME_MUTE, 1)
                label = "muted/unmuted (toggled)"
            elif act in {"up", "raise", "increase", "louder"}:
                _key(VK_VOLUME_UP, n)
                label = f"raised ({n} steps)"
            else:
                _key(VK_VOLUME_DOWN, n)
                label = f"lowered ({n} steps)"
            return {"status": "success", "message": f"System volume {label}.", "action": act}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


def try_system_control(message: str) -> str | None:
    """Fast-path natural language → brightness/volume without the action engine."""
    low = message.lower().strip()
    if not low:
        return None

    # Brightness
    if any(w in low for w in ("brightness", "screen bright", "dim the screen", "dim screen")):
        if any(w in low for w in ("lower", "down", "decrease", "dim", "reduce", "darker")):
            pct = _parse_percent(low)
            if pct is not None and ("to" in low or "%" in low):
                res = SystemControlTool.set_brightness(pct)
            else:
                res = SystemControlTool.adjust_brightness("down", 2)
        elif any(w in low for w in ("raise", "up", "increase", "brighter", "boost")):
            pct = _parse_percent(low)
            if pct is not None and ("to" in low or "%" in low):
                res = SystemControlTool.set_brightness(pct)
            else:
                res = SystemControlTool.adjust_brightness("up", 2)
        elif "set" in low or "%" in low:
            pct = _parse_percent(low, 50)
            res = SystemControlTool.set_brightness(pct if pct is not None else 50)
        else:
            cur = SystemControlTool.get_brightness()
            if cur.get("status") == "success":
                return f"Current screen brightness is {cur['level']}%."
            return None
        if res.get("status") == "success":
            return res.get("message") or "Brightness updated."
        return f"Couldn't change brightness: {res.get('message', 'unknown error')}"

    # Volume
    if any(w in low for w in ("volume", "sound", "mute", "unmute", "quieter", "louder")):
        if "unmute" in low:
            res = SystemControlTool.set_volume("unmute")
        elif "mute" in low or "silence" in low:
            res = SystemControlTool.set_volume("mute")
        elif any(w in low for w in ("lower", "down", "decrease", "reduce", "quieter", "quiet")):
            steps = 8 if "lot" in low or "much" in low else 5
            res = SystemControlTool.set_volume("down", steps)
        elif any(w in low for w in ("raise", "up", "increase", "louder", "boost")):
            steps = 8 if "lot" in low or "much" in low else 5
            res = SystemControlTool.set_volume("up", steps)
        else:
            return None
        if res.get("status") == "success":
            return res.get("message") or "Volume updated."
        return f"Couldn't change volume: {res.get('message', 'unknown error')}"

    return None
