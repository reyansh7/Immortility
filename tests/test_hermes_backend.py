"""Contract tests for the native Hermes agent/harness adapter."""

from __future__ import annotations

import asyncio
from urllib.error import HTTPError
from io import BytesIO

from core.hermes_backend import (
    KIND_INTERRUPTED,
    KIND_MALFORMED,
    KIND_RATE_LIMITED,
    KIND_TIMEOUT,
    KIND_UNAVAILABLE,
    HermesHTTPError,
    classify_hermes_failure,
    format_hermes_failure,
    public_event,
)


def test_nvidia_kimi_provider_configuration(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    from core.llm import _provider, local_api_key, local_base_url, local_model

    assert _provider() == "nvidia"
    assert local_base_url() == "https://integrate.api.nvidia.com/v1"
    assert local_model() == "moonshotai/kimi-k3"
    assert local_api_key() == "test-key"


def test_hermes_backend_waits_for_terminal_run(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_POLL_SECONDS", "0")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()
    replies = iter((
        {"run_id": "run_123"},
        {"status": "running", "session_id": "session_1"},
        {"status": "completed", "session_id": "session_1", "output": "Actual terminal result"},
    ))
    calls = []

    def fake_request(method, path, payload=None, timeout=None):
        calls.append((method, path, payload))
        return next(replies)

    monkeypatch.setattr(backend, "_request", fake_request)
    monkeypatch.setattr(backend, "_consume_sse", lambda *a, **k: None)
    result = asyncio.run(backend.run("whoami"))

    assert result.ok
    assert result.run_id == "run_123"
    assert result.session_id == "session_1"
    assert result.output == "Actual terminal result"
    assert calls[0][0:2] == ("POST", "/v1/runs")
    assert calls[0][2]["provider"] == "nvidia"
    assert calls[0][2]["model"] == "moonshotai/kimi-k3"
    assert calls[1][1] == "/v1/runs/run_123"


def test_hermes_backend_surfaces_unavailable_server(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()
    monkeypatch.setattr(backend, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("connection refused")))
    result = asyncio.run(backend.run("whoami"))

    assert not result.ok
    assert result.status == "failed"
    assert result.error_kind == KIND_UNAVAILABLE
    assert "connection refused" in result.error


def test_hermes_backend_preserves_http_429(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()

    def boom(*_args, **_kwargs):
        raise HermesHTTPError(429, "Hermes HTTP 429: rate limited by nvidia", retry_after="8")

    monkeypatch.setattr(backend, "_request", boom)
    result = asyncio.run(backend.run("whoami"))
    assert not result.ok
    assert result.error_kind == KIND_RATE_LIMITED
    assert result.retry_after == "8"
    assert "429" in format_hermes_failure(result)
    assert "simulated" in format_hermes_failure(result).lower()


def test_hermes_backend_timeout_and_malformed(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    monkeypatch.setenv("HERMES_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("HERMES_POLL_SECONDS", "0")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()
    monkeypatch.setattr(backend, "_request", lambda *_a, **_k: {"run_id": "run_slow", "status": "queued"})
    monkeypatch.setattr(backend, "_consume_sse", lambda *a, **k: None)
    timed = asyncio.run(backend.run("whoami"))
    assert timed.error_kind == KIND_TIMEOUT
    assert not timed.ok

    backend2 = HermesBackend()
    monkeypatch.setattr(
        backend2,
        "_request",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("Hermes returned malformed JSON")),
    )
    bad = asyncio.run(backend2.run("whoami"))
    assert bad.error_kind == KIND_MALFORMED


def test_hermes_backend_interrupted_run(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()
    replies = iter((
        {"run_id": "run_int"},
        {"status": "interrupted", "error": "cancelled by operator"},
    ))
    monkeypatch.setattr(backend, "_request", lambda *_a, **_k: next(replies))
    monkeypatch.setattr(backend, "_consume_sse", lambda *a, **k: None)
    result = asyncio.run(backend.run("whoami"))
    assert not result.ok
    assert result.status == "interrupted"
    assert result.error_kind == KIND_INTERRUPTED


def test_hermes_sse_completes_from_native_events(monkeypatch):
    monkeypatch.setenv("HERMES_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    from core.config import reset_config_cache
    reset_config_cache()
    from core.hermes_backend import HermesBackend

    backend = HermesBackend()
    observed = []

    def fake_sse(run_id, **kwargs):
        on_event = kwargs["on_event"]
        events = kwargs["events"]
        backend._emit({"event": "tool.started", "run_id": run_id, "tool": "terminal"}, events, on_event)
        backend._emit({"event": "tool.completed", "run_id": run_id, "tool": "terminal", "duration": 0.2}, events, on_event)
        return backend._result_from_status(
            {"status": "completed", "output": "reyan\nC:\\Users\\reyan", "session_id": "s1"},
            run_id=run_id,
            session_id="s1",
            events=events,
        )

    monkeypatch.setattr(backend, "_request", lambda *_a, **_k: {"run_id": "run_sse"})
    monkeypatch.setattr(backend, "_consume_sse", fake_sse)
    result = asyncio.run(backend.run("whoami", on_event=observed.append))
    assert result.ok
    assert result.output.startswith("reyan")
    tools = [e.get("tool") for e in result.events if e.get("tool")]
    assert "terminal" in tools
    assert observed


def test_public_event_strips_unknown_fields():
    cleaned = public_event({
        "event": "tool.started",
        "tool": "terminal",
        "Authorization": "Bearer secret",
        "command": "echo secret",
    })
    assert cleaned["event"] == "tool.started"
    assert cleaned["tool"] == "terminal"
    assert "Authorization" not in cleaned
    assert "command" not in cleaned


def test_classify_http_error_headers():
    err = HTTPError("http://x", 429, "rate", hdrs={"Retry-After": "5"}, fp=BytesIO(b"limit"))
    assert classify_hermes_failure("Hermes HTTP 429: limit", http_status=err.code) == KIND_RATE_LIMITED
