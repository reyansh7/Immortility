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
from core.pending_action import needs_confirmation, format_confirmation_message
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
    response = await asyncio.to_thread(
        chat, model="auto", messages=messages, think=False
    )
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


async def execute_action(
    user_input: str | None = None,
    require_edits: bool = False,
    internal_history: list | None = None,
    tools_executed_init: list | None = None,
    context_override: str = "",
    auto_confirm: bool = False,
) -> str:
    """Action Engine: LLM tool loop with path normalization and DONE enforcement.

    auto_confirm=True skips yes/no prompts (CLI convenience only — HUD must stay False).
    """
    state = AgentState()
    registry = ToolRegistry()
    registry.setup()

    from core.paths import get_desktop_path
    desktop = str(get_desktop_path())

    project_root = ""
    try:
        # KnowledgeEngine is a process-wide singleton — same instance as CLI `_get_engine()`
        from knowledge.engine import KnowledgeEngine

        active = KnowledgeEngine().get_active_project()
        if active and active.path:
            project_root = active.path
    except Exception:
        pass
    if not project_root and state.active_project:
        try:
            from memory.project_memory import ProjectMemory
            proj = ProjectMemory().get_project(state.active_project)
            if proj:
                project_root = proj.get("path", "")
        except Exception:
            pass

    if user_input:
        state.append_message("user", f"Execute request: {user_input}")

    example_path = (
        f"{project_root}\\src\\example.py" if project_root
        else f"{desktop}\\example.py"
    )

    from core.config import get_config, load_prompt_file
    from core.framework_hints import get_framework_hints
    from core.repo_paths import get_repo_root

    cfg = get_config()
    experience_block = ""
    try:
        from memory.experience_memory import ExperienceMemory, format_experiences_block

        if user_input:
            relevant = ExperienceMemory().get_relevant_experiences(user_input, limit=3)
            experience_block = format_experiences_block(relevant)
    except Exception:
        experience_block = ""

    ctx_for_prompt = context_override[:5000] if context_override else "No extra retrieved context provided."
    if experience_block:
        ctx_for_prompt = f"{ctx_for_prompt}\n\n{experience_block}"

    edit_mode_line = (
        "Execute real filesystem changes."
        if require_edits
        else "Investigate and answer — do NOT invent edits unless the user asked to fix/change code."
    )
    done_rule = (
        "NEVER call DONE until all required edits are applied AND verified with read_file."
        if require_edits
        else (
            "For analysis / bug-hunt / review / explain requests: gather enough evidence with "
            "read_file/list_directory (or tree), then call DONE with a clear Markdown report in "
            "args.message. Do NOT edit files. Do NOT keep reading forever — max ~8 reads then DONE."
        )
    )
    tool_body = load_prompt_file(
        "tool_system.txt",
        edit_mode_line=edit_mode_line,
        project_root=project_root or desktop,
        done_rule=done_rule,
        repo_root=str(get_repo_root()),
        context_override=ctx_for_prompt,
        framework_hints=get_framework_hints(project_root) or "- (none detected)",
        example_path=example_path,
        done_reason="verified changes" if require_edits else "analysis complete",
        done_message_hint=(
            "summary of what changed" if require_edits else "Markdown findings / bug report"
        ),
    )
    if not tool_body.strip():
        tool_body = (
            f"{edit_mode_line}\nProject root: {project_root or desktop}\n"
            f"{done_rule}\nRespond with ONE JSON tool call per turn."
        )
    tool_prompt = f"{registry.get_tool_prompt()}\n\n{tool_body}"

    console.print("\n[bold magenta]--- Action Engine ---[/bold magenta]")
    max_steps = (
        cfg.action_max_steps_readonly if not require_edits else cfg.action_max_steps_edit
    )
    files_read: set[str] = set()
    read_counts: dict[str, int] = {}
    tools_executed: set[str] = set(tools_executed_init or [])
    no_progress = 0
    file_edit_failures: dict[str, int] = {}
    last_error = ""
    verification_failures = 0
    done_reject_count = 0
    successful_lookups = 0
    evidence_notes: list[str] = []
    primary_model = "auto"
    current_model = primary_model
    using_local_fallback = False
    # When Gemini is primary, fall back to the configured local OpenAI-compatible model
    if _provider() == "gemini":
        fallback_model = local_model()
    else:
        fallback_model = _detect_fallback_model(local_model())
    before_snapshots: dict[str, str] = {}
    identical_failures: dict[str, int] = {}

    internal_history = list(internal_history) if internal_history else state.conversation_history[-8:].copy()
    if user_input:
        if not internal_history or internal_history[-1].get("content") != user_input:
            internal_history.append({"role": "user", "content": user_input})

    console.print(f"[dim]LLM backend: {active_backend()}[/dim]")

    async def _force_analysis_summary(reason: str) -> str:
        """Local models often never call DONE — synthesize a report from evidence."""
        bundle = "\n\n".join(evidence_notes[-12:])[:7000]
        if not bundle.strip():
            return (
                "I inspected the folder but couldn't finish a structured report. "
                "Try asking about one specific file (e.g. `rag/indexer.py`)."
            )
        try:
            from memory.user_profile import get_user_name

            who = get_user_name()
        except Exception:
            who = "the user"
        try:
            summary_tokens = get_config().action_summary_max_tokens
        except Exception:
            summary_tokens = 700
        prompt = (
            f"You analyzed a codebase via tools. Write a clear Markdown report for {who}.\n"
            f"Original request: {user_input or '(analysis)'}\n"
            f"Stop reason: {reason}\n\n"
            "Tool evidence:\n"
            f"{bundle}\n\n"
            "Include: what the folder/module is for, key files, how pieces connect, "
            "and any obvious issues. Be concrete. No tool JSON."
        )
        try:
            response = await asyncio.to_thread(
                chat,
                model="auto",
                messages=[
                    {
                        "role": "system",
                        "content": "You are Immortility. Summarize code investigation results briefly and clearly.",
                    },
                    {"role": "user", "content": prompt},
                ],
                think=False,
                max_output_tokens=summary_tokens,
                temperature=0.3,
            )
            summary = (response.get("message") or {}).get("content") or ""
            summary = str(summary).strip()
        except Exception as exc:
            summary = (
                f"Gathered notes from {successful_lookups} lookups "
                f"({len(files_read)} files read) but summary failed: {exc}"
            )
        if not summary:
            summary = (
                f"Looked at {len(files_read)} files / {successful_lookups} tool results "
                "but produced an empty summary. Ask about a specific file next."
            )
        console.print(Panel(Markdown(summary), title="✅ Action Completed", border_style="green"))
        state.append_message("assistant", f"Action completed: {summary}")
        return summary


    for step in range(max_steps):
        messages = [{"role": "system", "content": tool_prompt}] + internal_history
        try:
            response = await asyncio.to_thread(
                chat,
                model=current_model if using_local_fallback else "auto",
                messages=messages,
                think=False,
                force_provider="local" if using_local_fallback else None,
            )
        except Exception as exc:
            msg = f"LLM error: {exc}. Backend={active_backend()}"
            console.print(f"[red]{msg}[/red]")
            return msg

        content = response["message"]["content"]
        internal_history.append({"role": "assistant", "content": content})

        try:
            data = _parse_tool_json(content)
            tool_name = str(data.get("tool", "")).strip()
            args = data.get("args", {}) or {}
            wants_fallback = bool(data.get("request_fallback", False))

            if not tool_name:
                raise ValueError("Missing tool name")

            if tool_name.upper() == "DONE":
                if require_edits and not tools_executed.intersection(MUTATING_TOOLS):
                    done_reject_count += 1
                    # Don't loop forever — after 2 rejects, accept analysis-style DONE
                    if done_reject_count >= 2:
                        final_msg = args.get("message") or (
                            "Stopped: no edits were required/made. "
                            "Ask me to fix a specific issue if you want changes."
                        )
                        console.print(
                            Panel(Markdown(str(final_msg)), title="✅ Action Completed", border_style="green")
                        )
                        state.append_message("assistant", f"Action completed: {final_msg}")
                        return str(final_msg)
                    err = (
                        "DONE rejected: no file changes were made. "
                        "Use read_file, then edit_file/create_file/insert_after. "
                        "If the user only asked for analysis, call DONE with your findings."
                    )
                    console.print(f"[red]{err}[/red]")
                    internal_history.append({"role": "user", "content": err})
                    continue
                if wants_fallback and verification_failures >= 2 and fallback_model and not using_local_fallback:
                    using_local_fallback = True
                    current_model = fallback_model
                    msg = f"Switching to local fallback model: {fallback_model}"
                    _log_event("fallback_trigger", user_input or "", failure_reason=msg)
                    internal_history.append({"role": "user", "content": msg})
                    continue
                if wants_fallback and verification_failures >= 2 and fallback_model and current_model != fallback_model and using_local_fallback:
                    current_model = fallback_model
                    msg = f"Switching to fallback model: {fallback_model}"
                    _log_event("fallback_trigger", user_input or "", failure_reason=msg)
                    internal_history.append({"role": "user", "content": msg})
                    continue
                if tools_executed.intersection(MUTATING_TOOLS):
                    verification_ok, failure_note = await _verification_gate_on_done(
                        user_input or "",
                        project_root,
                        registry,
                        before_snapshots,
                    )
                    if not verification_ok:
                        verification_failures += 1
                        _log_event(
                            "verification_failure",
                            user_input or "",
                            verification_passed=False,
                            failure_reason=failure_note,
                        )
                        if verification_failures >= 3:
                            return f"Action failed verification after 3 attempts: {failure_note[:1200]}"
                        internal_history.append(
                            {
                                "role": "user",
                                "content": (
                                    "DONE rejected: verification failed.\n"
                                    f"{failure_note}\n"
                                    "Fix the issues and continue with tool JSON."
                                ),
                            }
                        )
                        continue
                final_msg = args.get("message", "Done")
                console.print(Panel(Markdown(final_msg), title="✅ Action Completed", border_style="green"))
                state.append_message("assistant", f"Action completed: {final_msg}")
                try:
                    from core.self_reflection import reflect_from_action_result

                    reflect_from_action_result(
                        user_input or "",
                        tools_executed=tools_executed,
                        message=str(final_msg),
                        success=True,
                        project=Path(project_root).name if project_root else None,
                    )
                except Exception:
                    pass
                return final_msg

            if "path" in args:
                args["path"] = _normalize_tool_path(args["path"], project_root, desktop)

            if tool_name == "read_file" and args.get("path"):
                path = args["path"]
                read_counts[path] = read_counts.get(path, 0) + 1
                max_reads = 2 if require_edits else 1
                if read_counts[path] > max_reads:
                    if require_edits:
                        err = (
                            f"You already read '{path}' {read_counts[path]} times. "
                            "Use write_file or edit_file to apply changes, or read a different file."
                        )
                    else:
                        err = (
                            f"You already read '{path}'. "
                            "Read a different file, or call DONE with your Markdown findings now."
                        )
                    console.print(f"[yellow]{err}[/yellow]")
                    internal_history.append({"role": "user", "content": err})
                    continue
                files_read.add(path)

            if tool_name in EDIT_TOOLS:
                path = args.get("path", "")
                if path and path not in files_read:
                    err = f"You MUST read_file('{path}') before editing it."
                    console.print(f"[red]{err}[/red]")
                    internal_history.append({"role": "user", "content": err})
                    continue

            if needs_confirmation(tool_name, args) and not auto_confirm:
                state.pending_action = {
                    "tool": tool_name,
                    "args": args,
                    "internal_history": internal_history[-6:],
                    "require_edits": require_edits,
                    "tools_executed": list(tools_executed),
                    "context_override": context_override[:5000] if context_override else "",
                }
                state.save()
                console.print(f"\n[bold yellow]{format_confirmation_message(tool_name, args)}[/bold yellow]")
                return format_confirmation_message(tool_name, args)

            console.print(f"\n[cyan][{step + 1}] {tool_name}[/cyan]")
            result = await registry.execute(tool_name, args)

            failed = _tool_call_failed(result)
            if not failed and tool_name == "read_file" and args.get("path"):
                path = args["path"]
                if path not in before_snapshots:
                    try:
                        parsed = json.loads(result)
                        raw = parsed.get("result", {}).get("content")
                        if isinstance(raw, str):
                            before_snapshots[path] = raw
                    except Exception:
                        pass
            if not failed and tool_name in MUTATING_TOOLS:
                tools_executed.add(tool_name)

            if failed:
                sig = f"{tool_name}|{json.dumps(args, sort_keys=True)}|{result[:300]}"
                identical_failures[sig] = identical_failures.get(sig, 0) + 1
                if identical_failures[sig] >= 2:
                    return (
                        "Tool call failed identically twice; halting to avoid unsafe retries.\n"
                        f"Tool: {tool_name}\nError: {result[:800]}"
                    )
                path = args.get("path", "")
                if tool_name in EDIT_TOOLS and path:
                    file_edit_failures[path] = file_edit_failures.get(path, 0) + 1

                extra_hint = ""
                if "Target text not found" in result:
                    extra_hint = (
                        "\nHINT: Copy target_text EXACTLY from read_file 'content' field (not numbered)."
                    )
                elif "syntax error" in result.lower() and path and file_edit_failures.get(path, 0) >= 2:
                    extra_hint = (
                        "\nHINT: Use write_file with the COMPLETE corrected file from read_file content."
                    )
                if extra_hint:
                    result = result.rstrip() + extra_hint

                no_progress += 1
                try:
                    data = json.loads(result.split("\nHINT:")[0])
                    inner = data.get("result", {})
                    last_error = inner.get("message", "") if isinstance(inner, dict) else str(inner)
                except (json.JSONDecodeError, AttributeError):
                    last_error = result[:200]
            else:
                no_progress = 0
                last_error = ""

            preview = result if isinstance(result, str) and len(result) < 2000 else str(result)[:2000]
            console.print(f"[green]Result:[/green] {preview[:400]}...")
            result_prefix = "Tool ERROR result" if failed else "Tool result"
            if not failed and tool_name in {"read_file", "list_directory", "run_command"}:
                successful_lookups += 1
                evidence_notes.append(f"### {tool_name}\n{preview[:1200]}")
            nudge = ""
            if (
                not require_edits
                and not failed
                and successful_lookups >= 5
            ):
                nudge = (
                    "\n\nCRITICAL: You already have enough evidence "
                    f"({successful_lookups} lookups, {len(files_read)} files). "
                    "Your NEXT response MUST be tool DONE with a Markdown report in args.message. "
                    "Do NOT read or list anything else."
                )
            internal_history.append(
                {"role": "user", "content": f"{result_prefix}: {preview}\n\nNext step? (JSON){nudge}"}
            )

            # Analysis mode: stop reading forever — force a written summary
            if not require_edits and successful_lookups >= 8:
                return await _force_analysis_summary("enough evidence gathered")

            if no_progress >= 3:
                console.print("[yellow]Repeated failures — stopping action loop.[/yellow]")
                if last_error:
                    console.print(f"[yellow]Last error: {last_error[:200]}[/yellow]")
                if not require_edits and evidence_notes:
                    return await _force_analysis_summary("repeated tool failures")
                break

        except json.JSONDecodeError:
            internal_history.append({"role": "user", "content": "Invalid JSON. Respond with one JSON object only."})
        except Exception as exc:
            internal_history.append({"role": "user", "content": f"Error: {exc}"})

    if not require_edits and evidence_notes:
        return await _force_analysis_summary("step limit reached without DONE")
    return (
        "Action loop ended without completion. "
        "Try asking about one specific file path (e.g. `rag/indexer.py`)."
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
