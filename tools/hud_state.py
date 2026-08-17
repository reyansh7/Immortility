"""Shared HUD face/status state — importable without starting the HTTP server."""

from __future__ import annotations

from typing import Any

_hud_state: dict[str, Any] = {
    "power": "98.4%",
    "network": "62%",
    "temp": "41.2C",
    "tasks": 4,
    "status": "ALL SUBSYSTEMS NOMINAL",
    "sync": "SYNCED",
    "face": "idle",
    "ollama_latency_ms": None,
}


def get_hud_state() -> dict[str, Any]:
    return dict(_hud_state)


def update_hud(**kwargs: Any) -> None:
    _hud_state.update({k: v for k, v in kwargs.items() if v is not None})


def set_face(mode: str, *, status: str | None = None, sync: str | None = None) -> None:
    mode = (mode or "idle").lower().strip()
    if mode not in {"idle", "listening", "speaking", "thinking"}:
        mode = "idle"
    payload: dict[str, Any] = {"face": mode}
    if status is not None:
        payload["status"] = status
    elif mode == "listening":
        payload["status"] = "LISTENING"
    elif mode == "speaking":
        payload["status"] = "SPEAKING"
    elif mode == "thinking":
        payload["status"] = "THINKING"
    else:
        payload["status"] = "SYNCED"

    if sync is not None:
        payload["sync"] = sync
    else:
        payload["sync"] = payload["status"]

    update_hud(**payload)
    # Ensure server is up so the HUD can poll (lazy; no-op if already running)
    try:
        from tools.hud_launcher import start_hud_server

        start_hud_server()
    except Exception:
        pass
