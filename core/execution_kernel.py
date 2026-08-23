"""Capability-agnostic execution kernel.

Generic primitives only: model, tool, task, cancel, timeout, retry, trace,
cache, permissions (delegated), concurrency, progress. No reference-project
special cases live here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from core.event_bus import EventBus
from core.harness import MODE_FAST, TraceEvent, begin_turn, current_scope, new_id, record
from core.pending_action import needs_confirmation

logger = logging.getLogger(__name__)

_READ_ONLY_CACHEABLE = frozenset(
    {
        "web_search",
        "search_google",
        "inspect_url",
        "read_file",
        "list_directory",
        "get_page_text",
        "get_current_url",
        "get_page_title",
    }
)


@dataclass
class KernelResult:
    ok: bool
    value: Any = None
    error: str = ""
    cancelled: bool = False
    cached: bool = False
    total_ms: int = 0
    ttft_ms: int | None = None


@dataclass
class BackgroundHandle:
    task_id: str
    status: str = "running"
    progress: str = ""
    result: str = ""
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)


class ExecutionKernel:
    """Process-wide kernel. Action Engine and HUD are clients."""

    _instance: ExecutionKernel | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._tasks: dict[str, BackgroundHandle] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="imm-kernel")
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> ExecutionKernel:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = ExecutionKernel()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._cancel.set()
            cls._instance = None

    def cancel_current(self) -> None:
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel = threading.Event()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def begin(self, *, mode: str = MODE_FAST, task_id: str | None = None) -> str:
        self.reset_cancel()
        return begin_turn(mode=mode, task_id=task_id)

    def emit_progress(self, message: str, *, percent: int | None = None) -> None:
        payload = {"message": message, "percent": percent}
        EventBus().publish("kernel.progress", payload)
        try:
            from tools.hud_state import update_hud

            status = message if percent is None else f"{message} ({percent}%)"
            update_hud(status=status[:120])
        except Exception:
            pass

    def run_model(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "brain",
        prefer_resident: bool = False,
        timeout_s: float | None = None,
        on_token: Callable[[str], None] | None = None,
        **chat_kwargs: Any,
    ) -> KernelResult:
        if self.cancelled():
            return KernelResult(False, error="cancelled", cancelled=True)
        started = time.perf_counter()
        ttft_ms: int | None = None
        tokens_out = 0
        model_id = ""
        retries = 0
        try:
            from models.router import select_for_role

            selection = select_for_role(role, prefer_resident=prefer_resident)
            if selection:
                model_id = selection.spec.model_id
        except Exception:
            pass

        def _on_token(piece: str) -> None:
            nonlocal ttft_ms, tokens_out
            if ttft_ms is None:
                ttft_ms = int((time.perf_counter() - started) * 1000)
            tokens_out += max(1, len(piece.split()))
            if on_token:
                on_token(piece)

        def _call() -> dict[str, Any]:
            from core.llm import chat, stream_chat

            if on_token is not None:
                text = stream_chat(
                    messages,
                    role=role,
                    on_token=_on_token,
                    **chat_kwargs,
                )
                return {"message": {"role": "assistant", "content": text}}
            return chat(model="auto", messages=messages, role=role, think=False, **chat_kwargs)

        try:
            if timeout_s and timeout_s > 0:
                future = self._pool.submit(_call)
                result = future.result(timeout=timeout_s)
            else:
                result = _call()
        except FuturesTimeout:
            total = int((time.perf_counter() - started) * 1000)
            record(
                TraceEvent(
                    kind="model",
                    model=model_id,
                    total_ms=total,
                    error="timeout",
                    retries=retries,
                )
            )
            return KernelResult(False, error="timeout", total_ms=total)
        except Exception as exc:
            total = int((time.perf_counter() - started) * 1000)
            record(
                TraceEvent(
                    kind="model",
                    model=model_id,
                    total_ms=total,
                    error=str(exc)[:300],
                    retries=retries,
                )
            )
            return KernelResult(False, error=str(exc), total_ms=total)

        text = str((result.get("message") or {}).get("content") or "")
        total = int((time.perf_counter() - started) * 1000)
        if ttft_ms is None and text:
            ttft_ms = total
        record(
            TraceEvent(
                kind="model",
                model=model_id,
                ttft_ms=ttft_ms,
                ttfu_ms=ttft_ms,
                total_ms=total,
                tokens_out=tokens_out or None,
            )
        )
        return KernelResult(True, value=result, total_ms=total, ttft_ms=ttft_ms)

    async def run_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = 60.0,
        use_cache: bool = True,
    ) -> KernelResult:
        args = args or {}
        if self.cancelled():
            return KernelResult(False, error="cancelled", cancelled=True)
        if needs_confirmation(name, args):
            # Kernel never auto-approves; caller/Action Engine owns the gate.
            pass
        if use_cache and name in _READ_ONLY_CACHEABLE:
            from core.tool_cache import get as cache_get

            hit = cache_get(name, args)
            if hit is not None:
                record(TraceEvent(kind="tool", tool=name, detail="cache_hit", total_ms=0))
                return KernelResult(True, value=hit, cached=True)

        started = time.perf_counter()
        EventBus().publish("kernel.before_tool", {"tool": name})

        async def _exec() -> str:
            from tools.tool_registry import ToolRegistry

            registry = ToolRegistry()
            registry.setup()
            return await registry.execute(name, args)

        try:
            if timeout_s and timeout_s > 0:
                result = await asyncio.wait_for(_exec(), timeout=timeout_s)
            else:
                result = await _exec()
        except asyncio.TimeoutError:
            total = int((time.perf_counter() - started) * 1000)
            record(TraceEvent(kind="tool", tool=name, total_ms=total, error="timeout"))
            EventBus().publish("kernel.after_tool", {"tool": name, "error": "timeout"})
            return KernelResult(False, error="timeout", total_ms=total)
        except Exception as exc:
            total = int((time.perf_counter() - started) * 1000)
            record(TraceEvent(kind="tool", tool=name, total_ms=total, error=str(exc)[:300]))
            EventBus().publish("kernel.after_tool", {"tool": name, "error": str(exc)})
            return KernelResult(False, error=str(exc), total_ms=total)

        total = int((time.perf_counter() - started) * 1000)
        record(TraceEvent(kind="tool", tool=name, total_ms=total))
        EventBus().publish("kernel.after_tool", {"tool": name, "ok": True})
        if use_cache and name in _READ_ONLY_CACHEABLE:
            from core.tool_cache import put as cache_put

            cache_put(name, args, result)
        return KernelResult(True, value=result, total_ms=total)

    async def run_tools_parallel(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        timeout_s: float = 60.0,
    ) -> list[KernelResult]:
        """Run independent read-oriented tools concurrently."""
        return list(
            await asyncio.gather(
                *[self.run_tool(name, args, timeout_s=timeout_s) for name, args in calls]
            )
        )

    def run_task(
        self,
        fn: Callable[[BackgroundHandle], None],
        *,
        ack: str = "Started background task.",
    ) -> tuple[str, BackgroundHandle]:
        task_id = new_id()
        handle = BackgroundHandle(task_id=task_id, progress="queued")
        with self._lock:
            self._tasks[task_id] = handle
        begin_turn(mode="BACKGROUND", task_id=task_id)
        self.emit_progress(ack)

        def _worker() -> None:
            started = time.perf_counter()
            try:
                fn(handle)
                if not handle.cancel.is_set():
                    handle.status = "done"
            except Exception as exc:
                handle.status = "error"
                handle.error = str(exc)
                record(
                    TraceEvent(
                        kind="task",
                        task_id=task_id,
                        error=str(exc)[:300],
                        total_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                return
            record(
                TraceEvent(
                    kind="task",
                    task_id=task_id,
                    total_ms=int((time.perf_counter() - started) * 1000),
                    cancelled=handle.cancel.is_set(),
                )
            )

        self._pool.submit(_worker)
        return ack + f" id={task_id}", handle

    def get_task(self, task_id: str) -> BackgroundHandle | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        handle = self.get_task(task_id)
        if not handle:
            return False
        handle.cancel.set()
        handle.status = "cancelled"
        return True


def get_kernel() -> ExecutionKernel:
    return ExecutionKernel.get()
