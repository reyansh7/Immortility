"""System vitals sampler for Immortility HUD (psutil + shared GPU probe)."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATS: dict[str, Any] = {
    "cpu_percent": 0.0,
    "mem_used_gb": 0.0,
    "mem_total_gb": 0.0,
    "gpu_percent": None,
    "vram_used_gb": None,
    "vram_total_gb": None,
    "ollama_latency_ms": None,
    "task_confidence": None,
}
_THREAD: threading.Thread | None = None
_STOP = threading.Event()


def get_cached_stats() -> dict[str, Any]:
    with _LOCK:
        out = dict(_STATS)
    try:
        from tools.hud_state import get_hud_state

        lat = get_hud_state().get("ollama_latency_ms")
        if lat is not None:
            out["ollama_latency_ms"] = lat
    except Exception:
        pass
    return out


def _sample_nvidia() -> tuple[float | None, float | None, float | None]:
    """Return (gpu_percent, vram_used_gb, vram_total_gb) from the shared probe.

    Same reading the model manager admits models against, so the HUD gauge and
    routing decisions can never disagree.
    """
    try:
        from models.vram import probe_gpu

        snapshot = probe_gpu(max_age_s=0.0)
    except Exception as exc:
        logger.debug("GPU probe unavailable: %s", exc)
        return None, None, None
    if not snapshot.available:
        return None, None, None
    used = None if snapshot.vram_used_mb is None else snapshot.vram_used_mb / 1024.0
    total = None if snapshot.vram_total_mb is None else snapshot.vram_total_mb / 1024.0
    return snapshot.gpu_percent, used, total


def _sample_once() -> None:
    # Note: first cpu_percent(interval=None) after process start can be 0.0 —
    # we prime once in start_stats_sampler before looping.
    try:
        import psutil

        cpu = float(psutil.cpu_percent(interval=None))
        vm = psutil.virtual_memory()
        # virtual_memory().percent tracks OS "Memory" used% closely on Windows;
        # used/total are physical (not commit charge).
        mem_used = float(vm.used) / (1024**3)
        mem_total = float(vm.total) / (1024**3)
    except Exception as exc:
        logger.debug("psutil sample failed: %s", exc)
        return

    gpu, vram_u, vram_t = _sample_nvidia()
    with _LOCK:
        _STATS["cpu_percent"] = round(cpu, 1)
        _STATS["mem_used_gb"] = round(mem_used, 2)
        _STATS["mem_total_gb"] = round(mem_total, 2)
        _STATS["gpu_percent"] = None if gpu is None else round(gpu, 1)
        _STATS["vram_used_gb"] = None if vram_u is None else round(vram_u, 2)
        _STATS["vram_total_gb"] = None if vram_t is None else round(vram_t, 2)


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _sample_once()
        except Exception:
            pass
        _STOP.wait(1.0)


def start_stats_sampler() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    try:
        import psutil

        # Prime so subsequent interval=None reads are meaningful
        psutil.cpu_percent(interval=0.0)
    except Exception:
        pass
    _sample_once()
    _THREAD = threading.Thread(target=_loop, name="hud-stats", daemon=True)
    _THREAD.start()
