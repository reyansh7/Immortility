"""Slice H — harness foundation: measurable executions, not a benchmark suite.

Every model / tool / task call can emit a trace. Slice 7 (full evaluation) will
consume these records later. This module does not know about reference tasks.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.repo_paths import events_log_path

MODE_FAST = "FAST"
MODE_AGENT = "AGENT"
MODE_BACKGROUND = "BACKGROUND"
KNOWN_MODES = frozenset({MODE_FAST, MODE_AGENT, MODE_BACKGROUND})

_lock = threading.Lock()
_last_turn: dict[str, Any] | None = None
_active_execution_id: str | None = None
_active_task_id: str | None = None
_active_mode: str = MODE_FAST


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def begin_turn(*, mode: str = MODE_FAST, task_id: str | None = None) -> str:
    """Start a user-turn scope. Returns execution_id."""
    global _active_execution_id, _active_task_id, _active_mode
    execution_id = new_id()
    with _lock:
        _active_execution_id = execution_id
        _active_task_id = task_id
        _active_mode = mode if mode in KNOWN_MODES else MODE_FAST
    return execution_id


def current_scope() -> tuple[str | None, str | None, str]:
    with _lock:
        return _active_execution_id, _active_task_id, _active_mode


@dataclass
class TraceEvent:
    kind: str
    execution_id: str = ""
    task_id: str = ""
    mode: str = MODE_FAST
    model: str = ""
    tool: str = ""
    ts: float = field(default_factory=time.time)
    ttft_ms: int | None = None
    ttfu_ms: int | None = None
    total_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str = ""
    retries: int = 0
    cancelled: bool = False
    vram_mb: float | None = None
    ram_mb: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "mode": self.mode,
            "model": self.model,
            "tool": self.tool,
            "timestamp": self.ts,
            "ttft_ms": self.ttft_ms,
            "ttfu_ms": self.ttfu_ms,
            "total_ms": self.total_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "error": self.error,
            "retries": self.retries,
            "cancelled": self.cancelled,
            "vram_mb": self.vram_mb,
            "ram_mb": self.ram_mb,
            "detail": self.detail,
        }


def _resources() -> tuple[float | None, float | None]:
    vram = None
    ram = None
    try:
        from models.vram import probe_gpu, probe_ram

        gpu = probe_gpu()
        if gpu.available:
            vram = gpu.vram_used_mb
        mem = probe_ram()
        if mem.available:
            ram = mem.used_mb
    except Exception:
        pass
    return vram, ram


def record(event: TraceEvent) -> None:
    """Append one trace line and remember the last turn summary."""
    global _last_turn
    if not event.execution_id:
        event.execution_id, event.task_id, event.mode = (
            event.execution_id or (current_scope()[0] or ""),
            event.task_id or (current_scope()[1] or ""),
            event.mode or current_scope()[2],
        )
    if event.vram_mb is None and event.ram_mb is None:
        event.vram_mb, event.ram_mb = _resources()
    row = event.to_dict()
    try:
        path = events_log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    except OSError:
        pass
    if event.kind in {"model", "tool", "task", "turn", "command", "coding"}:
        with _lock:
            _last_turn = row


def last_turn() -> dict[str, Any] | None:
    with _lock:
        return dict(_last_turn) if _last_turn else None


def last_turn_summary() -> str:
    row = last_turn()
    if not row:
        return "No execution traces yet."
    parts = [
        f"mode={row.get('mode') or '?'}",
        f"kind={row.get('kind')}",
    ]
    if row.get("model"):
        parts.append(f"model={row['model']}")
    if row.get("tool"):
        parts.append(f"tool={row['tool']}")
    if row.get("ttft_ms") is not None:
        parts.append(f"ttft={row['ttft_ms']}ms")
    if row.get("ttfu_ms") is not None:
        parts.append(f"ttfu={row['ttfu_ms']}ms")
    if row.get("total_ms") is not None:
        parts.append(f"total={row['total_ms']}ms")
    if row.get("error"):
        parts.append(f"error={row['error'][:80]}")
    if row.get("cancelled"):
        parts.append("cancelled")
    return "Last turn: " + " ".join(parts)


def reset_for_tests() -> None:
    global _last_turn, _active_execution_id, _active_task_id, _active_mode
    with _lock:
        _last_turn = None
        _active_execution_id = None
        _active_task_id = None
        _active_mode = MODE_FAST
