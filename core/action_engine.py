import asyncio
import difflib
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from core.llm import chat, active_backend, local_model, _provider

from core.agent_state import AgentState
from core.paths import normalize_path_key, sanitize_llm_path
from core.pending_action import (
    format_confirmation_message,
    with_user_confirmation,
)
from core.permissions import CONFIRM, DENY, decide, format_denial
from core.research_context import ResearchContext
from tools.tool_registry import ToolRegistry

console = Console()
logger = logging.getLogger(__name__)

MUTATING_TOOLS = frozenset({
    "create_file", "write_file", "edit_file", "replace_lines", "append_file",
    "insert_before", "insert_after", "replace_regex", "delete_block",
    "rename_symbol", "apply_patch", "delete_file",
})

EDIT_TOOLS = frozenset(MUTATING_TOOLS)


def _kernel_chat(messages: list, *, role: str = "brain", **kwargs):
    """Model call via the execution kernel (traces + timeouts)."""
    from core.execution_kernel import get_kernel

    result = get_kernel().run_model(messages, role=role, **kwargs)
    if not result.ok:
        raise RuntimeError(result.error or "model failed")
    return result.value


def _log_event(
    mode: str,
    query: str,
    retrieved: str = "",
    verification_passed: bool | None = None,
    failure_reason: str = "",
) -> None:
    path = Path("logs") / "immortility_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "query": query[:2000],
        "retrieved": retrieved[:5000],
        "verification_passed": verification_passed,
        "failure_reason": failure_reason[:2000],
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except OSError:
        logger.debug("Could not write immortility events log")


def _detect_fallback_model(primary: str) -> str:
    """Secondary model on the same OpenAI-compatible server (env only)."""
    configured = os.getenv("IMMORTILITY_FALLBACK_MODEL", "").strip()
    if configured and configured != primary:
        return configured
    return ""


def _tool_call_failed(result: str) -> bool:
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return '"status": "error"' in result
    if data.get("status") == "error":
        return True
    inner = data.get("result")
    return isinstance(inner, dict) and inner.get("status") == "error"


def _parse_tool_json(content: str) -> dict:
    from core.json_utils import parse_llm_json

    return parse_llm_json(content)


def _normalize_tool_path(path: str, project_root: str, desktop: str) -> str:
    if not path:
        return path
    root = project_root or desktop
    sanitized = sanitize_llm_path(path, root)
    if sanitized:
        return sanitized
    if project_root:
        return normalize_path_key(path, project_root)
    return str(Path(path).resolve())


async def agent_step(
    role: str,
    task: str,
    context: str = "",
    system_prompt: str = "",
    persist_history: bool = False,
) -> str:
    if not system_prompt:
        system_prompt = _load_system_prompt()
    prompt = f"Role: {role}\nTask: {task}"
    if context:
        prompt += f"\n\nContext:\n{context[:8000]}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    response = await asyncio.to_thread(_kernel_chat, messages, role="brain")
    content = response["message"]["content"]
    if persist_history:
        state = AgentState()
        state.append_message("user", task)
        state.append_message("assistant", content)
    return content


def _load_system_prompt() -> str:
    try:
        from core.config import load_prompt_file
        from core.repo_paths import get_repo_root

        try:
            from memory.user_profile import get_user_name

            user_name = get_user_name()
        except Exception:
            user_name = "there"
        text = load_prompt_file(
            "system.txt",
            user_name=user_name,
            repo_root=str(get_repo_root()),
        )
        if text.strip():
            return text
    except Exception:
        pass
    return "You are Immortility, a helpful local AI assistant."


async def _run_verification_gate(
    original_request: str,
    project_root: str,
    registry: ToolRegistry,
    before_snapshots: dict[str, str],
    full_ci: bool = True,
) -> tuple[bool, str]:
    """Mechanical (+ optional full CI) + semantic checks before allowing DONE."""
    from editing.verifier import ProjectVerifier

    roots = Path(project_root).resolve() if project_root else Path.cwd()
    touched = list(before_snapshots.keys())
    gate = ProjectVerifier(roots)

    if full_ci and project_root:
        ci_result = await gate.verify_project(touched)
        if not ci_result.get("success"):
            return False, (
                f"CI({ci_result.get('stage')}): {ci_result.get('details', '')[:2500]}"
            )
    else:
        file_result = await gate.verify_all(touched)
        if not file_result.get("success"):
            return False, f"Mechanical(file): {file_result.get('details', '')}"

    diff_parts: list[str] = []
    for path, before in before_snapshots.items():
        read_res = await registry.execute("read_file", {"path": path})
        try:
            data = json.loads(read_res)
            after = data.get("result", {}).get("content", "")
        except Exception:
            after = ""
        if not isinstance(after, str):
            after = ""
        if after != before:
            d = difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"{path}:before",
                tofile=f"{path}:after",
                lineterm="",
            )
            diff_parts.append("\n".join(d))
    diff_text = "\n\n".join(diff_parts)[:12000]

    reviewer_prompt = (
        "You are a strict independent reviewer. Given only the original request and diff, "
        "answer JSON: {\"pass\": true|false, \"reason\": \"...\"}. "
        "Pass only if diff completely satisfies request and matches surrounding code intent.\n\n"
        f"Request:\n{original_request}\n\nDiff:\n{diff_text or '(no diff)'}"
    )
    review = await asyncio.to_thread(
        chat,
        model="auto",
        messages=[{"role": "user", "content": reviewer_prompt}],
        think=False,
    )
    content = review["message"]["content"]
    try:
        parsed = _parse_tool_json(content)
        passes = bool(parsed.get("pass", False))
        reason = str(parsed.get("reason", "")).strip()
    except Exception:
        low = content.lower()
        passes = ("pass" in low) and ("fail" not in low)
        reason = content[:1000]
    if not passes:
        return False, f"Semantic(review): {reason or 'semantic reviewer rejected diff'}"
    return True, "verification passed"


async def _verification_gate_on_done(
    original_request: str,
    project_root: str,
    registry: ToolRegistry,
    before_snapshots: dict[str, str],
) -> tuple[bool, str]:
    """
    Mandatory gate on DONE path:
    Action Engine -> Verification Gate -> (pass => output, fail => self-correct loop).
    """
    return await _run_verification_gate(
        original_request=original_request,
        project_root=project_root,
        registry=registry,
        before_snapshots=before_snapshots,
    )


def _tool_call_sig(tool_name: str, args: dict | None) -> str:
    try:
        payload = json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        payload = str(args)
    return f"{tool_name}|{payload}"


async def resume_confirmed_pending(pending: dict) -> str:
    """Run a user-approved pending tool, then continue the loop with its result.

    HUD used to execute the tool and resume *without* injecting the result, so the
    model asked to run the same command again (yes/no loop).
    """
    tool_name = str((pending or {}).get("tool") or "")
    args = (pending or {}).get("args") or {}
    if not isinstance(args, dict):
        args = {}

    registry = ToolRegistry()
    registry.setup()
    result = await registry.execute(tool_name, with_user_confirmation(tool_name, args))
    preview = result if isinstance(result, str) and len(result) < 2000 else str(result)[:2000]

    resume_history = list((pending or {}).get("internal_history") or [])
    resume_history.append(
        {
            "role": "user",
            "content": (
                f"Tool result: {preview}\n\n"
                "This tool already ran successfully. Do NOT request the same tool/args again. "
                "Next step? (JSON) If you can answer the user, call DONE with a plain-text report."
            ),
        }
    )
    tools_executed_init = list((pending or {}).get("tools_executed") or [])
    if tool_name and tool_name not in tools_executed_init:
        tools_executed_init.append(tool_name)

    return await execute_action(
        None,
        require_edits=bool((pending or {}).get("require_edits", False)),
        internal_history=resume_history,
        tools_executed_init=tools_executed_init,
        auto_confirm=False,
        context_override=(pending or {}).get("context_override") or "",
        skip_confirm_sigs={_tool_call_sig(tool_name, args)},
    )


async def execute_action(
    user_input: str | None = None,
    require_edits: bool = False,
    internal_history: list | None = None,
    tools_executed_init: list | None = None,
    context_override: str = "",
    auto_confirm: bool = False,
    skip_confirm_sigs: set[str] | None = None,
) -> str:
    """Action Engine: LLM tool loop with path normalization and DONE enforcement.

    Dispatch order:
      1. Hermes optional backend     (backend == "hermes", requires HERMES_API_KEY + server)
      2. Native harness              (default)

    auto_confirm=True skips yes/no prompts (CLI convenience only — HUD must stay False).
    skip_confirm_sigs: tool+args already approved and executed this resume (do not re-prompt).
    """
    from core.config import get_config
    cfg = get_config()
    backend_name = (cfg.agent_backend or "immortality").lower()

    # ── Hermes optional backend ───────────────────────────────────────────────
    # Only activates when both the config selects hermes AND the API key is set.
    # Without a key Hermes refuses requests; fall through to the native harness.
    if backend_name == "hermes" and cfg.hermes_api_key and user_input:
        try:
            from core.hermes_backend import HermesBackend, format_hermes_failure, public_event

            def _on_hermes_event(event: dict) -> None:
                visible = public_event(event)
                kind = visible.get("event") or visible.get("type") or "event"
                tool = visible.get("tool")
                status = visible.get("status")
                if tool:
                    console.print(f"[dim]Hermes {kind}: {tool}[/dim]")
                elif status:
                    console.print(f"[dim]Hermes {kind}: {status}[/dim]")

            result = await HermesBackend().run(
                user_input,
                context=context_override,
                require_edits=require_edits,
                on_event=_on_hermes_event,
            )
            _log_event(
                "HERMES",
                user_input,
                retrieved=context_override,
                verification_passed=result.ok,
                failure_reason=result.error or result.error_kind,
            )
            if result.ok:
                console.print(
                    f"[dim]Hermes run {result.run_id or '?'} completed "
                    f"({len(result.events)} observed events)[/dim]"
                )
                return result.output
            return format_hermes_failure(result)
        except Exception as exc:
            logger.exception("Hermes backend integration failure")
            return f"Hermes integration error: {exc}"

    return await _run_native_loop(
        user_input=user_input,
        require_edits=require_edits,
        internal_history=internal_history,
        tools_executed_init=tools_executed_init,
        context_override=context_override,
        auto_confirm=auto_confirm,
        skip_confirm_sigs=skip_confirm_sigs,
    )


async def _run_native_loop(
    user_input: str | None = None,
    require_edits: bool = False,
    internal_history: list | None = None,
    tools_executed_init: list | None = None,
    context_override: str = "",
    auto_confirm: bool = False,
    skip_confirm_sigs: set[str] | None = None,
    on_event: object = None,
) -> str:
    """Native tool loop: plan only when the request is a multi-step edit."""
    from core.harness.engine import ImmortalityHarness

    harness = ImmortalityHarness()
    if tools_executed_init:
        harness.tool_executor.executed_tools.update(tools_executed_init)

    goal = user_input or (
        internal_history[-1].get("content") if internal_history else "Execute request"
    )

    return await harness.run(
        goal=goal,
        require_edits=require_edits,
        context_override=context_override,
        auto_confirm=auto_confirm,
        internal_history=internal_history,
        on_event=on_event,
        skip_confirm_sigs=skip_confirm_sigs,
    )



async def handle_summarize_webpage() -> str:
    registry = ToolRegistry()
    registry.setup()
    result = await registry.execute("get_page_text", {})
    data = json.loads(result)
    if data.get("status") != "success":
        return "Could not read the current page."

    text = data["result"].get("text", "")[:8000]
    url = data["result"].get("url", "")

    response = await asyncio.to_thread(
        chat,
        model="auto",
        messages=[{"role": "user", "content": f"Summarize this webpage ({url}):\n\n{text}"}],
        think=False,
    )
    summary = response["message"]["content"]
    AgentState().set_research_context(ResearchContext(
        query="summarize webpage",
        urls=[url] if url else [],
        sources=[url] if url else [],
        extracted_text=text[:4000],
        summary=summary,
    ))
    return summary


def is_summarize_request(text: str) -> bool:
    lower = text.lower()
    return "summarize" in lower and ("page" in lower or "webpage" in lower or "website" in lower)


def is_page_query(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in ("what page", "current url", "where am i", "what site", "what tab"))


def is_coding_request(text: str) -> bool:
    from core.intent import is_coding_intent

    return is_coding_intent(text)
