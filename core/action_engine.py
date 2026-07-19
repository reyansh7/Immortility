import asyncio
import difflib
import json
import logging
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from ollama import chat

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
    configured = os.getenv("IMMORTILITY_FALLBACK_MODEL", "").strip()
    if configured:
        return configured
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=8, check=False
        )
        if out.returncode != 0:
            return ""
        candidates = []
        for line in out.stdout.splitlines()[1:]:
            name = line.split()[0].strip()
            low = name.lower()
            if name == primary:
                continue
            if any(x in low for x in ("14b", "27b", "32b", "34b", "70b")):
                candidates.append(name)
        return candidates[0] if candidates else ""
    except Exception:
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
    import re
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    if "```json" in content:
        json_str = content.split("```json")[-1].split("```")[0].strip()
    elif "```" in content:
        json_str = content.split("```")[-1].split("```")[0].strip()
    else:
        json_str = content.strip()
    return json.loads(json_str)


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
        chat, model="qwen3:8b", messages=messages, think=False
    )
    content = response["message"]["content"]
    if persist_history:
        state = AgentState()
        state.append_message("user", task)
        state.append_message("assistant", content)
    return content


def _load_system_prompt() -> str:
    try:
        with open("prompts/system.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are Immortility, a helpful local AI assistant."


async def _run_verification_gate(
    original_request: str,
    project_root: str,
    registry: ToolRegistry,
    before_snapshots: dict[str, str],
    full_ci: bool = True,
) -> tuple[bool, str]:
    """Mechanical (+ optional full CI) + semantic checks before allowing DONE."""
    from editing.ci_gate import CIGate
    from editing.verifier import Verifier

    failures: list[str] = []
    roots = Path(project_root).resolve() if project_root else Path.cwd()
    touched = list(before_snapshots.keys())

    # Iterative speed path already happened during edits; completion requires full CI
    if full_ci and project_root:
        ci = CIGate(roots)
        ci_result = ci.run_full_pipeline(touched)
        if not ci_result.get("success"):
            return False, (
                f"CI({ci_result.get('stage')}): {ci_result.get('details', '')[:2500]}"
            )
    else:
        for path in before_snapshots:
            status = Verifier.verify_file(path, roots)
            if status != "PASS":
                failures.append(f"Mechanical(file): {path}: {status}")
        if failures:
            return False, "\n".join(failures)

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
        model="qwen3:8b",
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
) -> str:
    """Action Engine: LLM tool loop with path normalization and DONE enforcement."""
    state = AgentState()
    registry = ToolRegistry()
    registry.setup()

    from core.paths import get_desktop_path
    desktop = str(get_desktop_path())

    project_root = ""
    try:
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

    tool_prompt = f"""
{registry.get_tool_prompt()}

You are the Action Engine for Immortility. Execute real filesystem changes.

RULES:
1. Read before edit — call read_file before any edit tool on the same file.
2. NEW files only → create_file. Existing files → edit_file, insert_after, replace_lines, write_file.
3. ALL paths must be absolute. Project root: {project_root or desktop}
4. read_file returns raw text in "content" and line numbers in "numbered" — use "content" for target_text, "numbered" for replace_lines.
5. For .tsx/.jsx files: prefer write_file with the FULL corrected file if edit_file fails twice.
6. NEVER call DONE until all required edits are applied AND verified with read_file.
7. Respond with ONE JSON object per turn: tool, reason, confidence, args.
8. NEVER use search_google or open_url for local file paths.
9. If read_file returns "No such file or directory", the file does not exist. You MUST use create_file to create it.
10. To list installed software or games on Windows, use run_command with this EXACT command (do not modify dollar signs):
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.UTF8Encoding]::UTF8; Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | Where-Object {{ $_.DisplayName }} | Select-Object -ExpandProperty DisplayName | Sort-Object -Unique"
Filter game names from the list in your DONE message. Do NOT repeat the command if it already succeeded.
11. When asked to retrieve, list, or read information, your final DONE message MUST contain the ACTUAL data/list beautifully formatted in Markdown. Do NOT just say "I listed the files" — actually provide the list to the user!
12. If asked to run/start the project, use run_command to launch the dev servers (e.g. npm run dev, uvicorn, python). It will return RUNNING. You MUST include the localhost URLs (e.g. http://localhost:3000) in your final DONE message so the user can click them.
13. If asked to read, list, or scan an "entire folder" or project recursively, DO NOT use list_directory in a loop! Use run_command with: powershell -Command "tree /F '<path>'" to get the entire folder structure instantly.
14. If verification repeatedly fails, you may return {{"tool":"DONE","reason":"fallback","confidence":1.0,"request_fallback":true,"args":{{"message":"need stronger model"}}}} only after at least two failed verification attempts.
15. Use this read-only retrieved context before tool calls:
{context_override[:5000] if context_override else "No extra retrieved context provided."}

Example read:
{{"tool": "read_file", "reason": "read before edit", "confidence": 1.0, "args": {{"path": "{example_path}"}}}}

Example insert:
{{"tool": "insert_after", "reason": "add import", "confidence": 0.95, "args": {{"path": "{example_path}", "target_text": "exact line from content field", "content": "new line"}}}}

Example write full file (use when small file or edit_file keeps failing):
{{"tool": "write_file", "reason": "rewrite file", "confidence": 0.9, "args": {{"path": "{example_path}", "content": "full file text"}}}}

Example done (only after edits verified):
{{"tool": "DONE", "reason": "verified changes", "confidence": 1.0, "args": {{"message": "summary of what changed"}}}}
"""

    console.print("\n[bold magenta]--- Action Engine ---[/bold magenta]")
    max_steps = 20
    files_read: set[str] = set()
    read_counts: dict[str, int] = {}
    tools_executed: set[str] = set(tools_executed_init or [])
    no_progress = 0
    file_edit_failures: dict[str, int] = {}
    last_error = ""
    verification_failures = 0
    primary_model = "qwen3:8b"
    current_model = primary_model
    fallback_model = _detect_fallback_model(primary_model)
    before_snapshots: dict[str, str] = {}
    identical_failures: dict[str, int] = {}

    internal_history = list(internal_history) if internal_history else state.conversation_history[-8:].copy()
    if user_input:
        if not internal_history or internal_history[-1].get("content") != user_input:
            internal_history.append({"role": "user", "content": user_input})

    for step in range(max_steps):
        messages = [{"role": "system", "content": tool_prompt}] + internal_history
        try:
            response = await asyncio.to_thread(
                chat, model=current_model, messages=messages, think=False
            )
        except Exception as exc:
            msg = f"Ollama error: {exc}. Is Ollama running with qwen3:8b?"
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
                    err = (
                        "DONE rejected: no file changes were made. "
                        "Use read_file, then edit_file/create_file/insert_after."
                    )
                    console.print(f"[red]{err}[/red]")
                    internal_history.append({"role": "user", "content": err})
                    continue
                if wants_fallback and verification_failures >= 2 and fallback_model and current_model != fallback_model:
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
                return final_msg

            if "path" in args:
                args["path"] = _normalize_tool_path(args["path"], project_root, desktop)

            if tool_name == "read_file" and args.get("path"):
                path = args["path"]
                read_counts[path] = read_counts.get(path, 0) + 1
                if read_counts[path] > 2:
                    err = (
                        f"You already read '{path}' {read_counts[path]} times. "
                        "Use write_file or edit_file to apply changes, or read a different file."
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

            if needs_confirmation(tool_name, args):
                state.pending_action = {
                    "tool": tool_name,
                    "args": args,
                    "internal_history": internal_history[-6:],
                    "require_edits": require_edits,
                    "tools_executed": list(tools_executed)
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
            internal_history.append({"role": "user", "content": f"{result_prefix}: {preview}\n\nNext step? (JSON)"})

            if no_progress >= 3:
                console.print("[yellow]Repeated failures — stopping action loop.[/yellow]")
                if last_error:
                    console.print(f"[yellow]Last error: {last_error[:200]}[/yellow]")
                break

        except json.JSONDecodeError:
            internal_history.append({"role": "user", "content": "Invalid JSON. Respond with one JSON object only."})
        except Exception as exc:
            internal_history.append({"role": "user", "content": f"Error: {exc}"})

    return "Action loop ended without completion. Try rephrasing or use a more specific file path."


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
        model="qwen3:8b",
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
    lower = text.lower()
    return any(kw in lower for kw in (
        "implement", "fix", "refactor", "rename", "add ", "update file", "convert",
        "create file", "create component", "jwt", "authentication", "make the changes", "edit file", "modify file",
    ))
