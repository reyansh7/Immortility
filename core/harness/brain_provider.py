"""BrainProvider implementations for the IMMORTALITY Custom Harness.

Decouples model invocation from specific vendors (Kimi K3 / NVIDIA / Ollama / etc.)
by routing through the process-wide ExecutionKernel.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from core.harness.contracts import BrainProvider

logger = logging.getLogger(__name__)


class KernelBrainProvider:
    """Default BrainProvider: routes model reasoning through core.execution_kernel.

    Supports role-based routing (brain, code, vision), VRAM management, timeouts,
    streaming callbacks, and fallback detection.
    """

    def __init__(self, fallback_model: str = "") -> None:
        self.fallback_model = fallback_model
        self.using_fallback = False

    async def generate_response(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "brain",
        timeout_s: float | None = None,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> str:
        if self.using_fallback and self.fallback_model:
            from core.llm import chat
            res = await asyncio.to_thread(
                chat,
                model=self.fallback_model,
                messages=messages,
                role=role,
                think=False,
                force_provider="local",
                **kwargs,
            )
            return str((res.get("message") or {}).get("content") or "")

        try:
            from core.action_engine import _kernel_chat
            raw = await asyncio.to_thread(_kernel_chat, messages, role=role, timeout_s=timeout_s, **kwargs)
            if isinstance(raw, dict):
                return str((raw.get("message") or {}).get("content") or "")
            return str(raw)
        except Exception as exc:
            if self.fallback_model and not self.using_fallback:
                logger.warning("KernelBrainProvider primary failed: %s; switching to fallback", exc)
                self.using_fallback = True
                return await self.generate_response(
                    messages,
                    role=role,
                    timeout_s=timeout_s,
                    on_token=on_token,
                    **kwargs,
                )
            raise RuntimeError(f"Brain reasoning failed: {exc}")

        raw = kres.value
        if isinstance(raw, dict):
            return str((raw.get("message") or {}).get("content") or "")
        return str(raw)


class MockBrainProvider:
    """Deterministic BrainProvider for unit testing."""

    def __init__(self, canned_responses: list[str] | None = None) -> None:
        self.responses = list(canned_responses or [])
        self.call_history: list[list[dict[str, Any]]] = []

    async def generate_response(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str = "brain",
        timeout_s: float | None = None,
        **kwargs: Any,
    ) -> str:
        self.call_history.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return json.dumps({"tool": "DONE", "args": {"message": "Mock completion"}})
