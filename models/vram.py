"""Shared GPU / RAM probing.

Single implementation of the ``nvidia-smi`` read used by both the HUD vitals
sampler and the model manager, so the numbers on screen and the numbers the
router admits models against can never disagree.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: tuple[float, "GpuSnapshot"] | None = None
DEFAULT_MAX_AGE_S = 2.0


@dataclass(frozen=True)
class GpuSnapshot:
    """One nvidia-smi reading. ``available`` is False when there is no NVIDIA GPU."""

    available: bool
    gpu_percent: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    detail: str = ""

    @property
    def vram_free_mb(self) -> float | None:
        if self.vram_used_mb is None or self.vram_total_mb is None:
            return None
        return max(0.0, self.vram_total_mb - self.vram_used_mb)


@dataclass(frozen=True)
class RamSnapshot:
    available: bool
    used_mb: float | None = None
    total_mb: float | None = None

    @property
    def free_mb(self) -> float | None:
        if self.used_mb is None or self.total_mb is None:
            return None
        return max(0.0, self.total_mb - self.used_mb)


def _run_nvidia_smi() -> GpuSnapshot:
    if not shutil.which("nvidia-smi"):
        return GpuSnapshot(available=False, detail="nvidia-smi not on PATH")
    try:
        # utilization.gpu matches what Task Manager's GPU tab approximates.
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        lines = (proc.stdout or "").strip().splitlines()
        if not lines:
            return GpuSnapshot(available=False, detail="nvidia-smi returned no rows")
        parts = [p.strip() for p in lines[0].split(",")]
        if len(parts) < 3:
            return GpuSnapshot(available=False, detail="unexpected nvidia-smi output")
        return GpuSnapshot(
            available=True,
            gpu_percent=float(parts[0]),
            vram_used_mb=float(parts[1]),
            vram_total_mb=float(parts[2]),
        )
    except Exception as exc:
        logger.debug("nvidia-smi failed: %s", exc)
        return GpuSnapshot(available=False, detail=f"nvidia-smi failed: {exc}")


def probe_gpu(max_age_s: float = DEFAULT_MAX_AGE_S) -> GpuSnapshot:
    """Cached GPU reading. Pass ``max_age_s=0`` to force a fresh sample."""
    global _CACHE
    now = time.monotonic()
    if max_age_s > 0:
        with _LOCK:
            cached = _CACHE
        if cached and (now - cached[0]) <= max_age_s:
            return cached[1]
    snapshot = _run_nvidia_smi()
    with _LOCK:
        _CACHE = (now, snapshot)
    return snapshot


def probe_ram() -> RamSnapshot:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return RamSnapshot(
            available=True,
            used_mb=float(vm.used) / (1024**2),
            total_mb=float(vm.total) / (1024**2),
        )
    except Exception as exc:
        logger.debug("psutil memory probe failed: %s", exc)
        return RamSnapshot(available=False)


def reset_cache() -> None:
    """Drop the cached GPU reading — used by tests."""
    global _CACHE
    with _LOCK:
        _CACHE = None
