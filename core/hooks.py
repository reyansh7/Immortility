"""Deterministic coding-loop hooks. Observable, cancellable, not an LLM.

Hooks do not plan, edit, or run shell commands. They inject rules, skip
redundant inspect, assemble a fresh review packet, and record traces.
Permissions, confirmation, timeouts, and subprocesses stay in the existing
kernels (pending_action, execution_kernel, command_tool).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from core.event_bus import EventBus
from core.harness import MODE_AGENT, TraceEvent, record

logger = logging.getLogger(__name__)

BEFORE_PLAN = "before_plan"
BEFORE_INSPECT = "before_inspect"
BEFORE_EDIT = "before_edit"
BEFORE_EXECUTE = "before_execute"
AFTER_EXECUTE = "after_execute"
BEFORE_REVIEW = "before_review"
AFTER_REVIEW = "after_review"
BEFORE_FINALIZE = "before_finalize"

LIFECYCLE = (
    BEFORE_PLAN,
    BEFORE_INSPECT,
    BEFORE_EDIT,
    BEFORE_EXECUTE,
    AFTER_EXECUTE,
    BEFORE_REVIEW,
    AFTER_REVIEW,
    BEFORE_FINALIZE,
)


@dataclass
class HookResult:
    ok: bool = True
    skip: bool = False
    cancelled: bool = False
    message: str = ""
    extra_context: str = ""
    data: dict[str, Any] = field(default_factory=dict)


HookHandler = Callable[[dict[str, Any]], HookResult | None]


def _kernel_cancelled() -> bool:
    try:
        from core.execution_kernel import get_kernel

        return get_kernel().cancelled()
    except Exception:
        return False


def _builtin_before_plan(payload: dict[str, Any]) -> HookResult:
    request = str(payload.get("request") or "")
    try:
        from skills.registry import get_skill_registry

        block = get_skill_registry().prompt_block(request)
    except Exception as exc:
        logger.debug("skill inject: %s", exc)
        block = ""
    return HookResult(ok=True, extra_context=block, data={"skills": bool(block)})


def _builtin_before_inspect(payload: dict[str, Any]) -> HookResult:
    simple = bool(payload.get("simple"))
    return HookResult(ok=True, skip=simple, message="light inspect" if simple else "")


def _builtin_before_edit(payload: dict[str, Any]) -> HookResult:
    # Confirmation stays in execute_action / pending_action. Hook only observes.
    return HookResult(ok=True, data={"role": "coder"})


def _builtin_before_execute(payload: dict[str, Any]) -> HookResult:
    argv = payload.get("argv") or []
    if argv:
        try:
            from core.coding_engine import allowlisted_check_argv

            cwd = payload.get("cwd") or "."
            if allowlisted_check_argv(list(argv), cwd) is None:
                return HookResult(ok=False, message="check argv rejected by allowlist")
        except Exception as exc:
            logger.debug("before_execute allowlist: %s", exc)
    return HookResult(ok=True)


def _builtin_after_execute(payload: dict[str, Any]) -> HookResult:
    ok = bool(payload.get("ok"))
    return HookResult(ok=ok, data={"check_ok": ok})


def _builtin_before_review(payload: dict[str, Any]) -> HookResult:
    # Fresh packet only — never attach coder conversation.
    packet = {
        "goal": str(payload.get("goal") or "")[:500],
        "changed_paths": list(payload.get("changed_paths") or [])[:40],
        "has_diff": bool(payload.get("patch")),
        "check_ok": payload.get("check_ok"),
    }
    return HookResult(ok=True, data={"packet": packet})


def _builtin_after_review(payload: dict[str, Any]) -> HookResult:
    ok = bool(payload.get("ok", True))
    findings = list(payload.get("findings") or [])
    return HookResult(ok=ok, extra_context="\n".join(findings)[:4000], data={"review_ok": ok})


def _builtin_before_finalize(payload: dict[str, Any]) -> HookResult:
    check_ok = bool(payload.get("check_ok"))
    review_ok = payload.get("review_ok")
    if review_ok is None:
        review_ok = True
    if not check_ok or not review_ok:
        return HookResult(ok=False, message="not verified")
    return HookResult(ok=True, message="verified")


_BUILTIN: dict[str, list[HookHandler]] = {
    BEFORE_PLAN: [_builtin_before_plan],
    BEFORE_INSPECT: [_builtin_before_inspect],
    BEFORE_EDIT: [_builtin_before_edit],
    BEFORE_EXECUTE: [_builtin_before_execute],
    AFTER_EXECUTE: [_builtin_after_execute],
    BEFORE_REVIEW: [_builtin_before_review],
    AFTER_REVIEW: [_builtin_after_review],
    BEFORE_FINALIZE: [_builtin_before_finalize],
}


class HookRunner:
    """Runs builtin + extra handlers. Extra handlers cannot bypass cancel."""

    def __init__(self) -> None:
        self._extra: dict[str, list[HookHandler]] = {name: [] for name in LIFECYCLE}

    def add(self, hook: str, handler: HookHandler) -> None:
        if hook not in self._extra:
            raise ValueError(f"unknown hook: {hook}")
        self._extra[hook].append(handler)

    def run(self, hook: str, payload: dict[str, Any] | None = None) -> HookResult:
        payload = dict(payload or {})
        if _kernel_cancelled():
            result = HookResult(ok=False, cancelled=True, message="cancelled")
            self._observe(hook, result)
            return result
        combined = HookResult(ok=True)
        for handler in (*_BUILTIN.get(hook, ()), *self._extra.get(hook, ())):
            try:
                piece = handler(payload) or HookResult(ok=True)
            except Exception as exc:
                logger.warning("hook %s handler error: %s", hook, exc)
                piece = HookResult(ok=False, message=str(exc)[:200])
            if piece.cancelled or _kernel_cancelled():
                piece.cancelled = True
                piece.ok = False
                self._observe(hook, piece)
                return piece
            combined.ok = combined.ok and piece.ok
            combined.skip = combined.skip or piece.skip
            if piece.message:
                combined.message = piece.message
            if piece.extra_context:
                combined.extra_context = (
                    f"{combined.extra_context}\n{piece.extra_context}".strip()
                )
            combined.data.update(piece.data)
            if piece.extra_context:
                payload["extra_context"] = combined.extra_context
        self._observe(hook, combined)
        return combined

    def _observe(self, hook: str, result: HookResult) -> None:
        record(
            TraceEvent(
                kind="coding",
                tool=f"hook:{hook}",
                detail=(result.message or hook)[:200],
                error="" if result.ok else (result.message or "hook failed")[:200],
                mode=MODE_AGENT,
            )
        )
        EventBus().publish(
            f"coding.hook.{hook}",
            {"ok": result.ok, "skip": result.skip, "cancelled": result.cancelled},
        )


_runner: HookRunner | None = None


def get_hook_runner() -> HookRunner:
    global _runner
    if _runner is None:
        _runner = HookRunner()
    return _runner


def reset_hook_runner() -> None:
    global _runner
    _runner = None
    EventBus.reset()
