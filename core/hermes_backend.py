"""Hermes Agent/Harness adapter using Hermes' native asynchronous runs API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.agent_backend import AgentRunResult
from core.config import get_config

logger = logging.getLogger(__name__)

KIND_RATE_LIMITED = "rate_limited"
KIND_TIMEOUT = "timeout"
KIND_UNAVAILABLE = "unavailable"
KIND_MALFORMED = "malformed"
KIND_INTERRUPTED = "interrupted"
KIND_UNAUTHENTICATED = "unauthenticated"
KIND_FAILED = "failed"

_TERMINAL_OK = frozenset({"completed", "succeeded", "success"})
_TERMINAL_FAIL = frozenset({"failed", "error"})
_TERMINAL_STOP = frozenset({"cancelled", "canceled", "interrupted"})
_PUBLIC_EVENT_KEYS = (
    "event",
    "type",
    "run_id",
    "status",
    "tool",
    "preview",
    "duration",
    "error",
    "session_id",
)


class HermesHTTPError(RuntimeError):
    def __init__(self, status: int, message: str, retry_after: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.kind = classify_hermes_failure(message, http_status=status)


def classify_hermes_failure(text: str, *, http_status: int | None = None) -> str:
    if http_status == 429:
        return KIND_RATE_LIMITED
    if http_status in {401, 403}:
        return KIND_UNAUTHENTICATED
    if http_status in {502, 503, 504}:
        return KIND_UNAVAILABLE
    raw = (text or "").lower()
    if "429" in raw or "rate limit" in raw or "too many requests" in raw:
        return KIND_RATE_LIMITED
    if "timed out" in raw or "timeout" in raw:
        return KIND_TIMEOUT
    if "malformed" in raw or "invalid json" in raw or "invalid response envelope" in raw:
        return KIND_MALFORMED
    if "unavailable" in raw or "connection refused" in raw or "10061" in raw or "winerror 10061" in raw:
        return KIND_UNAVAILABLE
    if "interrupted" in raw or "cancelled" in raw or "canceled" in raw:
        return KIND_INTERRUPTED
    if "401" in raw or "403" in raw or "unauthorized" in raw or "api key" in raw:
        return KIND_UNAUTHENTICATED
    return KIND_FAILED


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop secrets and oversized payloads before logging or UI display."""
    out: dict[str, Any] = {}
    for key in _PUBLIC_EVENT_KEYS:
        value = event.get(key)
        if value is None or value == "":
            continue
        text = str(value)
        out[key] = text[:240]
    return out


def format_hermes_failure(result: AgentRunResult) -> str:
    kind = result.error_kind or classify_hermes_failure(result.error)
    detail = (result.error or "No final result was returned.").strip()
    extra = f" Retry-After: {result.retry_after}." if result.retry_after else ""
    prefix = {
        KIND_RATE_LIMITED: "Hermes/Kimi rate limited (HTTP 429).",
        KIND_TIMEOUT: "Hermes run timed out.",
        KIND_UNAVAILABLE: "Hermes API server is unavailable.",
        KIND_MALFORMED: "Hermes returned a malformed response.",
        KIND_INTERRUPTED: "Hermes run was interrupted.",
        KIND_UNAUTHENTICATED: "Hermes rejected the API key.",
        KIND_FAILED: "Hermes execution failed.",
    }.get(kind, "Hermes execution failed.")
    return (
        f"{prefix} {detail}{extra} "
        "No tool result was simulated. Start the loopback Hermes gateway "
        "(`hermes gateway run`) and verify HERMES_BASE_URL / HERMES_API_KEY."
    )


class HermesBackend:
    """Delegate executable work to a resident Hermes API server.

    Hermes creates the Kimi-backed agent, invokes its harness/tools, observes
    results, and completes the closed loop.  This adapter deliberately does not
    parse or execute tool calls itself.
    """

    def __init__(self) -> None:
        cfg = get_config()
        self.base_url = cfg.hermes_base_url.rstrip("/")
        self.api_key = cfg.hermes_api_key
        self.timeout = cfg.hermes_timeout_seconds
        self.poll_seconds = cfg.hermes_poll_seconds

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": accept}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(self.base_url + path, data=data, headers=self._headers(), method=method)
        wait = timeout if timeout is not None else min(self.timeout, 30.0)
        try:
            with urlopen(req, timeout=wait) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            retry_after = str(exc.headers.get("Retry-After") or "")
            raise HermesHTTPError(
                exc.code,
                f"Hermes HTTP {exc.code}: {body or exc.reason}",
                retry_after=retry_after,
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"Hermes unavailable at {self.base_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Hermes timed out talking to {self.base_url}{path}") from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError("Hermes returned malformed JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Hermes returned an invalid response envelope")
        return parsed

    @staticmethod
    def _extract_output(payload: dict[str, Any]) -> str:
        for key in ("output", "result", "message", "text", "response", "final_response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested in ("output", "content", "text", "message"):
                    item = value.get(nested)
                    if isinstance(item, str) and item.strip():
                        return item.strip()
        return ""

    def _emit(self, event: dict[str, Any], events: list[dict[str, Any]], on_event: Callable | None) -> None:
        cleaned = public_event(event)
        if not cleaned:
            return
        events.append(cleaned)
        kind = cleaned.get("event") or cleaned.get("type") or "hermes.event"
        tool = cleaned.get("tool") or ""
        logger.info(
            "hermes event run=%s event=%s tool=%s status=%s",
            cleaned.get("run_id") or "",
            kind,
            tool,
            cleaned.get("status") or "",
        )
        if on_event:
            try:
                on_event(cleaned)
            except Exception:
                logger.debug("Immortility Hermes event callback failed", exc_info=True)

    def _failed_result(
        self,
        *,
        error: str,
        run_id: str = "",
        events: list[dict[str, Any]] | None = None,
        session_id: str = "",
        status: str = "failed",
        retry_after: str = "",
        http_status: int | None = None,
    ) -> AgentRunResult:
        kind = classify_hermes_failure(error, http_status=http_status)
        if status in _TERMINAL_STOP:
            kind = KIND_INTERRUPTED
        return AgentRunResult(
            status=status if status in _TERMINAL_STOP else "failed",
            run_id=run_id,
            session_id=session_id,
            events=events or [],
            error=error,
            error_kind=kind,
            retry_after=retry_after,
        )

    def _result_from_status(
        self,
        payload: dict[str, Any],
        *,
        run_id: str,
        session_id: str,
        events: list[dict[str, Any]],
    ) -> AgentRunResult | None:
        status = str(payload.get("status") or "unknown").lower()
        output = self._extract_output(payload)
        sid = str(payload.get("session_id") or session_id)
        if status in _TERMINAL_OK:
            if not output.strip():
                return self._failed_result(
                    error="Hermes completed without an observed tool/result payload",
                    run_id=run_id,
                    events=events,
                    session_id=sid,
                )
            return AgentRunResult(
                status="completed",
                output=output,
                run_id=run_id,
                session_id=sid,
                events=events,
            )
        if status in _TERMINAL_STOP:
            return self._failed_result(
                error=str(payload.get("error") or f"Hermes run {status}"),
                run_id=run_id,
                events=events,
                session_id=sid,
                status="interrupted",
            )
        if status in _TERMINAL_FAIL:
            return self._failed_result(
                error=str(payload.get("error") or f"Hermes run {status}"),
                run_id=run_id,
                events=events,
                session_id=sid,
            )
        return None

    def _consume_sse(
        self,
        run_id: str,
        *,
        session_id: str,
        events: list[dict[str, Any]],
        on_event: Callable | None,
        deadline: float,
    ) -> AgentRunResult | None:
        req = Request(
            self.base_url + f"/v1/runs/{run_id}/events",
            headers=self._headers(accept="text/event-stream"),
            method="GET",
        )
        remaining = max(1.0, deadline - time.monotonic())
        try:
            response = urlopen(req, timeout=remaining)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            retry_after = str(exc.headers.get("Retry-After") or "")
            raise HermesHTTPError(exc.code, f"Hermes HTTP {exc.code}: {body or exc.reason}", retry_after) from exc
        except (URLError, TimeoutError, OSError):
            return None
        buffer = ""
        try:
            with response:
                while time.monotonic() < deadline:
                    raw = response.readline()
                    if raw == b"":
                        break
                    line = raw.decode("utf-8", errors="replace").strip("\r\n")
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        raise RuntimeError("Hermes returned malformed JSON")
                    if not isinstance(payload, dict):
                        continue
                    event_name = str(payload.get("event") or payload.get("type") or "")
                    self._emit({**payload, "type": event_name or "hermes.event"}, events, on_event)
                    if event_name.startswith("run."):
                        synthetic = dict(payload)
                        synthetic["status"] = event_name.split(".", 1)[-1]
                        terminal = self._result_from_status(
                            synthetic, run_id=run_id, session_id=session_id, events=events
                        )
                        if terminal is not None:
                            return terminal
        finally:
            try:
                response.close()
            except Exception:
                pass
        return None

    def _model_and_provider(self) -> tuple[str, str]:
        model = "moonshotai/kimi-k3"
        provider = "nvidia"
        try:
            from core.llm import local_model

            model = local_model(provider="nvidia") or model
        except Exception:
            pass
        return model, provider

    async def run(
        self,
        request: str,
        *,
        context: str = "",
        session_id: str = "",
        require_edits: bool = False,
        on_event: Any = None,
    ) -> AgentRunResult:
        model, provider = self._model_and_provider()
        instructions = (
            "You are Hermes, the execution layer for Immortility. Use your real tools and skills "
            "to complete the request. Observe tool results, recover from failures, and verify the outcome. "
            "Do not claim an action succeeded unless Hermes actually executed and observed it. "
            + (
                "The user requested a change; obey Hermes approval and safety policies. "
                if require_edits
                else "Keep this read-only unless the user explicitly authorizes a change. "
            )
            + (f"Immortility context:\n{context[:12000]}" if context else "")
        )
        body: dict[str, Any] = {
            "input": request,
            "instructions": instructions,
            "model": model,
            "provider": provider,
        }
        if session_id:
            body["session_id"] = session_id
        started = time.monotonic()
        deadline = started + self.timeout
        logger.info(
            "hermes request provider=%s model=%s base=%s chars=%s",
            provider,
            model,
            self.base_url,
            len(request),
        )
        try:
            created = await asyncio.to_thread(self._request, "POST", "/v1/runs", body)
        except HermesHTTPError as exc:
            logger.warning("Hermes run creation failed: %s", exc)
            return self._failed_result(
                error=str(exc),
                retry_after=exc.retry_after,
                http_status=exc.status,
            )
        except Exception as exc:
            logger.warning("Hermes run creation failed: %s", exc)
            return self._failed_result(error=str(exc))
        run_id = str(created.get("run_id") or created.get("id") or "")
        if not run_id:
            return self._failed_result(error="Hermes accepted no run_id")
        events: list[dict[str, Any]] = []
        self._emit(
            {"type": "hermes.accepted", "run_id": run_id, "status": str(created.get("status") or "started")},
            events,
            on_event,
        )

        sse_result: AgentRunResult | None = None
        try:
            sse_result = await asyncio.to_thread(
                self._consume_sse,
                run_id,
                session_id=session_id,
                events=events,
                on_event=on_event,
                deadline=deadline,
            )
        except HermesHTTPError as exc:
            return self._failed_result(
                error=str(exc),
                run_id=run_id,
                events=events,
                retry_after=exc.retry_after,
                http_status=exc.status,
            )
        except Exception as exc:
            logger.info("Hermes SSE unavailable, polling run status: %s", exc)
        if sse_result is not None:
            return sse_result

        while time.monotonic() < deadline:
            try:
                status_payload = await asyncio.to_thread(
                    self._request, "GET", f"/v1/runs/{run_id}", None
                )
            except HermesHTTPError as exc:
                return self._failed_result(
                    error=str(exc),
                    run_id=run_id,
                    events=events,
                    retry_after=exc.retry_after,
                    http_status=exc.status,
                )
            except Exception as exc:
                return self._failed_result(error=str(exc), run_id=run_id, events=events)
            status = str(status_payload.get("status") or "unknown").lower()
            self._emit(
                {"type": "hermes.status", "run_id": run_id, "status": status},
                events,
                on_event,
            )
            terminal = self._result_from_status(
                status_payload, run_id=run_id, session_id=session_id, events=events
            )
            if terminal is not None:
                return terminal
            await asyncio.sleep(self.poll_seconds)
        return self._failed_result(
            error=f"Hermes run timed out after {self.timeout:.0f}s",
            run_id=run_id,
            events=events,
        )
