"""Execution kernel: timeout, cancel, traces. No reference-task special cases."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from core.execution_kernel import ExecutionKernel, get_kernel
from core.harness import last_turn, record, reset_for_tests, TraceEvent
from core.llm import assemble_stream_chunks
from core.tool_cache import clear as clear_tool_cache
from core.tool_cache import get as cache_get
from core.tool_cache import put as cache_put


def test_assemble_stream_chunks():
    assert assemble_stream_chunks(["Hel", "lo", "!"]) == "Hello!"


def test_kernel_has_no_reference_catalog():
    src = Path("core/execution_kernel.py").read_text(encoding="utf-8").lower()
    for needle in ("pdf chat", "resume analyzer", "25 task", "instagram"):
        assert needle not in src


def test_tool_cache_roundtrip():
    clear_tool_cache()
    assert cache_get("inspect_url", {"url": "https://example.com"}) is None
    cache_put("inspect_url", {"url": "https://example.com"}, '{"ok": true}')
    assert cache_get("inspect_url", {"url": "https://example.com"}) == '{"ok": true}'
    clear_tool_cache()


def test_kernel_timeout_on_fake_model(monkeypatch):
    reset_for_tests()
    kernel = ExecutionKernel()
    kernel.reset_cancel()

    def slow_chat(**kwargs):
        time.sleep(2)
        return {"message": {"role": "assistant", "content": "late"}}

    monkeypatch.setattr("core.llm.chat", slow_chat)
    monkeypatch.setattr(
        "core.llm.stream_chat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no stream")),
    )
    result = kernel.run_model(
        [{"role": "user", "content": "hi"}],
        timeout_s=0.05,
    )
    assert not result.ok
    assert result.error == "timeout"


def test_kernel_cancel():
    kernel = ExecutionKernel()
    kernel.cancel_current()
    result = kernel.run_model([{"role": "user", "content": "hi"}])
    assert result.cancelled
    kernel.reset_cancel()


def test_harness_records_last_turn():
    reset_for_tests()
    record(TraceEvent(kind="model", mode="FAST", model="qwythos9b-q4", total_ms=12, ttft_ms=4))
    row = last_turn()
    assert row is not None
    assert row["model"] == "qwythos9b-q4"
    assert row["ttft_ms"] == 4


def test_get_kernel_singleton():
    ExecutionKernel.reset_instance()
    a = get_kernel()
    b = get_kernel()
    assert a is b
    ExecutionKernel.reset_instance()
