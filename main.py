import asyncio
import json
import logging
import threading
from logging.handlers import RotatingFileHandler

# pyrefly: ignore [missing-import]
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.markdown import Markdown
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML

from pathlib import Path

from memory.memory_manager import MemoryManager
from tools.browser_manager import BrowserManager
from tools.tool_registry import setup_registry
from core.agent_state import AgentState
from core.router import classify_route
from core.pending_action import is_approval, is_rejection
from core.project_extract import extract_open_path, is_implementation_confirm, is_run_project_request, extract_open_path_only
from core.text_sanitize import sanitize_text
from core.action_engine import (
    agent_step,
    execute_action,
    handle_summarize_webpage,
    is_summarize_request,
    is_page_query,
    is_coding_request,
    _load_system_prompt,
)
from agents.research_agent import ResearchAgent
from skills.leetcode_skill import LeetCodeSkill
from tools.tool_registry import ToolRegistry

console = Console()

# ── Logging setup ───────────────────────────────────────────────────

def _setup_logging() -> None:
    from core.config import get_config
    from core.repo_paths import main_log_path

    cfg = get_config()
    log_path = main_log_path()
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=cfg.log_max_bytes,
        backupCount=cfg.log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root.setLevel(logging.INFO)
    root.addHandler(handler)


_setup_logging()
# Suppress noisy third-party loggers
for name in ("turbovec", "sentence_transformers", "httpx", "urllib3", "openai"):
    logging.getLogger(name).setLevel(logging.WARNING)

# ── Knowledge Engine (lazy-loaded) ──────────────────────────────────

_knowledge_engine = None
_voice_enabled = False


def _get_engine():
    """Lazy-load the Knowledge Engine to avoid slow startup."""
    global _knowledge_engine
    if _knowledge_engine is None:
        from knowledge.engine import KnowledgeEngine
        console.print("[dim]Initialising Knowledge Engine...[/dim]")
        _knowledge_engine = KnowledgeEngine()
        console.print("[dim]Knowledge Engine ready.[/dim]")
    return _knowledge_engine


from core.intent import HEAVY_CHAT_HINTS, STOP_PHRASES, is_coding_intent, is_heavy_chat


def _looks_like_fast_chat(text: str) -> bool:
    """True for short conversational turns that should skip RAG/routing."""
    t = (text or "").strip()
    try:
        from core.config import get_config

        char_limit = get_config().fast_chat_char_limit
    except Exception:
        char_limit = 140
    if not t or len(t) > char_limit:
        return False
    if t.startswith("/"):
        return False
    low = t.lower()
    if is_heavy_chat(t) or is_coding_intent(t):
        return False
    # Path-like tokens → project/RAG path
    if ("\\" in t or "/" in t) and ("." in t or "src" in low or "desktop" in low):
        return False
    # Code questions that look short but need retrieval
    code_q = (
        "what does",
        "how does",
        "where is",
        "where does",
        "function do",
        "in the code",
        "this file",
        "that file",
        "look at",
        "show me the",
    )
    if any(q in low for q in code_q):
        return False
    return True


# Back-compat aliases
_HEAVY_CHAT_HINTS = HEAVY_CHAT_HINTS
_STOP_PHRASES = STOP_PHRASES


async def _speak_if_enabled(text: str) -> None:
    """Speak a short summary when voice mode is on (full text stays on screen)."""
    if not _voice_enabled or not text:
        return
    try:
        from tools.voice_io import get_voice

        voice = get_voice()
        brief = voice.spoken_brief(text)
        console.print(f"[bold cyan]🔊 Speaking (short):[/bold cyan] [dim]{brief[:120]}…[/dim]" if len(brief) > 120 else f"[bold cyan]🔊 Speaking (short):[/bold cyan] {brief}")
        await asyncio.to_thread(voice.speak, text)  # speak() already briefs
        console.print("[dim](done speaking)[/dim]")
    except Exception as exc:
        console.print(f"[red]Voice speak failed: {exc}[/red]")
        logging.exception("TTS failed")


async def handle_desktop_projects_request(user_input: str) -> str:
    """Answer Desktop/Projects questions from a live scan — natural, not a fat dump."""
    from core.desktop_scanner import (
        answer_desktop_projects_query,
        list_project_folders,
        projects_dir,
    )

    reply = answer_desktop_projects_query(user_input, learn=True)
    folders = list_project_folders()

    console.print("\n[bold blue][DESKTOP/PROJECTS][/bold blue]")
    console.print(f"[dim]Live scan of {projects_dir()} — {len(folders)} folders[/dim]")
    console.print(Panel(Markdown(reply), title="🧠 Immortility", border_style="cyan"))

    state = AgentState()
    state.append_message("user", user_input)
    state.append_message("assistant", reply)
    state.save()
    return reply


async def run_speech_to_speech(memory: str) -> None:
    """Continuous speech↔speech loop using the configured LLM (Gemini primary).

    Mic → Whisper (offline) → Immortility LLM → male Windows TTS → repeat.
    Say "stop" / "exit" / "goodbye" or press Ctrl+C to leave.
    """
    global _voice_enabled
    from core.desktop_scanner import (
        wants_projects_scan,
        whisper_vocabulary_hint,
    )
    from core.llm import active_backend
    from tools.voice_io import get_voice

    _voice_enabled = True
    console.print(
        Panel(
            f"Speech-to-speech via [bold]{active_backend()}[/bold].\n"
            "Speak after the prompt. Say [bold]stop[/bold] while it talks to interrupt, "
            "or [bold]stop[/bold] on your turn / Ctrl+C to leave.\n"
            "[dim]Asks about Desktop/Projects use a live folder scan (not RAG guesses).[/dim]",
            title="🎙️ Talk mode",
            border_style="magenta",
        )
    )
    try:
        with console.status("[bold cyan]Loading Whisper + male TTS…[/bold cyan]", spinner="dots"):
            await asyncio.to_thread(get_voice().ensure_ready)
        voice_name = await asyncio.to_thread(get_voice().set_male_voice)
        console.print(f"[green]Ready.[/green] Voice: {voice_name}")
    except Exception as exc:
        console.print(f"[red]Could not start talk mode: {exc}[/red]")
        console.print("[dim]Need: pip install faster-whisper sounddevice numpy pyttsx3[/dim]")
        _voice_enabled = False
        return

    try:
        await _speak_if_enabled(
            "Talk mode is on. While I am speaking, say stop to interrupt me."
        )

        state = AgentState()
        vocab = whisper_vocabulary_hint()
        while True:
            try:
                console.print("\n[bold cyan]🎤 Your turn — speak now[/bold cyan]")
                with console.status("[bold cyan]Listening (offline Whisper)…[/bold cyan]", spinner="dots"):
                    heard = await asyncio.to_thread(
                        lambda: get_voice().listen(initial_prompt=vocab)
                    )
                heard = (heard or "").strip()
                if not heard:
                    console.print("[yellow]Nothing heard — try again.[/yellow]")
                    continue

                console.print(f"[green]You:[/green] {heard}")
                if heard.lower().strip("!.?") in _STOP_PHRASES or heard.lower().startswith("stop "):
                    await _speak_if_enabled("Okay, ending talk mode.")
                    console.print("[magenta]Talk mode ended.[/magenta]")
                    break

                # Ground-truth filesystem when user asks about projects / desktop
                if wants_projects_scan(heard):
                    from core.desktop_scanner import (
                        answer_desktop_projects_query,
                        spoken_projects_brief,
                    )

                    report = answer_desktop_projects_query(heard, learn=True)
                    console.print(
                        Panel(Markdown(report), title="🧠 Immortility", border_style="cyan")
                    )
                    state.append_message("user", heard)
                    state.append_message("assistant", report)
                    state.save()
                    await _speak_if_enabled(spoken_projects_brief())
                    continue

                with console.status(f"[bold cyan]🧠 {active_backend()}…[/bold cyan]", spinner="bouncingBar"):
                    try:
                        from tools.hud_state import set_face

                        set_face("thinking")
                    except Exception:
                        pass
                    from core.llm import fast_chat
                    from core.config import get_config

                    hist = [
                        {"role": m["role"], "content": m["content"]}
                        for m in (state.conversation_history or [])[-6:]
                        if m.get("role") in {"user", "assistant"} and m.get("content")
                    ]
                    reply = await asyncio.to_thread(
                        fast_chat,
                        heard,
                        history=hist,
                        max_output_tokens=get_config().speech_max_tokens,
                    )
                    try:
                        from tools.hud_state import set_face

                        set_face("idle")
                    except Exception:
                        pass
                state.mode = "CHAT"
                state.append_message("user", heard)
                state.append_message("assistant", reply)
                state.save()
                console.print(Panel(Markdown(reply), title="🧠 Immortility", border_style="cyan"))
                await _speak_if_enabled(reply)
            except KeyboardInterrupt:
                console.print("\n[magenta]Talk mode interrupted.[/magenta]")
                break
            except Exception as exc:
                msg = str(exc)
                low = msg.lower()
                if "out of memory" in low or "cudamalloc" in low:
                    console.print(
                        "[red]Talk turn failed:[/red] Local fallback needed more GPU memory.\n"
                        "[dim]Using llama3.2:3b + CPU retry — restart Immortility to load the fix.[/dim]"
                    )
                elif "quota" in low or "429" in low or "resource_exhausted" in low:
                    console.print(
                        "[yellow]Gemini quota hit[/yellow] — local fallback also failed.\n"
                        "[dim]Wait ~1 min or check https://ai.dev/rate-limit — then try again.[/dim]"
                    )
                else:
                    console.print(f"[red]Talk turn failed:[/red] {exc}")
                logging.exception("speech-to-speech turn failed")
                try:
                    await _speak_if_enabled(
                        "Sorry, I hit a brain glitch. Try saying that again."
                    )
                except Exception:
                    pass
    finally:
        _voice_enabled = False

# ── Pending action handler (unchanged from Phase 1) ─────────────────


async def handle_pending_action(user_input: str) -> bool:
    """
    Process pending action if one exists.
    Returns True if handled (caller should continue loop).
    """
    state = AgentState()
    state.reload()
    if not state.pending_action:
        return False

    registry = ToolRegistry()
    registry.setup()

    if is_approval(user_input):
        pending = state.pending_action
        tool_name = pending["tool"]
        args = pending["args"]
        require_edits = pending.get("require_edits", False)
        resume_history = pending.get("internal_history")
        console.print(f"\n[cyan]Confirmed — executing:[/cyan] {tool_name}")

        result = await registry.execute(tool_name, args)
        console.print(f"[green]Result:[/green] {result[:500]}")

        state.pending_action = None
        if pending.get("workflow") == "leetcode" or (
            state.current_task and state.current_task.get("workflow") == "leetcode"
        ):
            state.current_task = state.current_task or {}
            state.current_task["step"] = "Complete"
            state.save()
            console.print("[bold green]LeetCode solution saved to desktop.[/bold green]")
            return True

        if resume_history:
            resume_history = list(resume_history)
            resume_history.append({
                "role": "user",
                "content": f"Tool result: {result}\n\nNext step? (JSON)",
            })
        tools_executed_init = pending.get("tools_executed", [])
        await execute_action(
            require_edits=require_edits, 
            internal_history=resume_history,
            tools_executed_init=tools_executed_init
        )
        return True

    if is_rejection(user_input):
        console.print("[red]Action cancelled.[/red]")
        state.pending_action = None
        if state.current_task:
            state.current_task["step"] = "Cancelled"
        state.append_message("user", "User denied confirmation. Tool was NOT executed.")
        state.save()
        return True

    console.print(
        "[bold yellow]You have a pending action awaiting confirmation. "
        "Reply yes to proceed or no to cancel.[/bold yellow]"
    )
    return True


def _looks_like_actionable_plan(response: str) -> bool:
    lower = response.lower()
    markers = (
        "let me implement",
        "i'll create",
        "i will create",
        "make these changes",
        "file changes:",
        "verification:",
        "implementation plan:",
    )
    return any(m in lower for m in markers)


async def handle_pending_coding_plan(user_input: str) -> bool:
    """Execute a stored coding plan when user confirms (yes / make the changes)."""
    state = AgentState()
    pending = state.pending_coding_request
    if not pending:
        return False
    if not is_implementation_confirm(user_input) and not is_approval(user_input):
        return False

    engine = _get_engine()
    active = engine.get_active_project()
    if not active or not active.path:
        console.print("[red]Open a project first with /open <path>[/red]")
        return True

    if isinstance(pending, dict):
        request = pending.get("request", "Implement the proposed changes")
        planner_hint = pending.get("plan", "")
    else:
        request = str(pending)
        planner_hint = ""

    console.print("\n[bold blue][PROJECT][/bold blue]")
    console.print("[cyan]Implementing confirmed plan...[/cyan]")

    from editing.coding_workflow import CodingWorkflow
    context = engine.get_context(request)
    workflow = CodingWorkflow(project_root=active.path, rag_context=context)
    result = await workflow.run(request, planner_hint=planner_hint)

    state.pending_coding_request = None
    state.save()

    if result.success:
        console.print(f"[bold green]{result.message}[/bold green]")
    else:
        console.print(f"[red]{result.message}[/red]")
        console.print("[yellow]Falling back to Action Engine...[/yellow]")
        task = (
            f"Project root: {active.path}\n"
            f"Request: {request}\n"
            f"Plan:\n{planner_hint[:4000]}\n"
            f"Apply all file changes. Read each file before editing."
        )
        await execute_action(task, require_edits=True)
    return True


async def handle_run_project(project_path: str, user_input: str) -> None:
    """Start frontend/backend dev servers and print URLs."""
    from core.project_runner import ProjectRunner
    from rich.panel import Panel

    console.print("\n[bold cyan]=== Starting Project ===[/bold cyan]")
    console.print(f"[dim]Project: {project_path}[/dim]")

    try:
        services = ProjectRunner.detect_services(project_path)
        if not any(services.values()):
            console.print("[red]No runnable frontend or backend detected in this project.[/red]")
            return

        started = ProjectRunner.start(project_path)
        lines = ["**Servers started** (running in background)\n"]
        for item in started:
            lines.append(f"- **{item['service']}**: {item['url']}")
            if item.get("login"):
                lines.append(f"  - Login: {item['login']}")
            if item.get("docs"):
                lines.append(f"  - API docs: {item['docs']}")

        lines.append("\nOpen the frontend URL in your browser. Backend must be running for login.")
        console.print(Panel(Markdown("\n".join(lines)), title="🚀 Project Running", border_style="green"))
    except Exception as exc:
        console.print(f"[red]Failed to start project: {exc}[/red]")


async def _run_coding_workflow(
    project_path: str,
    user_request: str,
    engine,
) -> None:
    """Run CodingWorkflow with optional Action Engine fallback."""
    context = engine.get_context(user_request)
    from editing.coding_workflow import CodingWorkflow, run_symbol_rename

    lower = user_request.lower()
    if "rename" in lower and " to " in lower:
        import re
        m = re.search(r"rename\s+(\w+)\s+to\s+(\w+)", lower)
        if m:
            result = await run_symbol_rename(project_path, m.group(1), m.group(2))
        else:
            workflow = CodingWorkflow(project_root=project_path, rag_context=context)
            result = await workflow.run(user_request)
    else:
        workflow = CodingWorkflow(project_root=project_path, rag_context=context)
        result = await workflow.run(user_request)

    if result.success:
        console.print(f"[bold green]{result.message}[/bold green]")
    else:
        console.print(f"[red]{result.message}[/red]")
        console.print("[yellow]Falling back to Action Engine...[/yellow]")
        task = (
            f"Project root: {project_path}\n"
            f"Request: {user_request}\n"
            f"Context:\n{context[:6000]}\n"
            f"Apply changes using edit_file, create_file, insert_after tools. "
            f"Read each file before editing. Use src/app/ paths for Next.js App Router."
        )
        await execute_action(task, require_edits=True)


# ── Workflow execution (extended with RAG context) ──────────────────


async def execute_workflow(user_input: str, memory: str, choice: str = "4", is_research: bool = False):
    state = AgentState()
    
    if "leetcode" in user_input.lower():
        skill = LeetCodeSkill()
        await skill.run(user_input)
        return
    
    if is_research:
        researcher = ResearchAgent()
        await researcher.execute(user_input)
    
    if choice == "4":
        console.print("\n[bold magenta]=== Phase 3 Autonomous Workflow ===[/bold magenta]")
        from core.workflow_engine import WorkflowEngine
        
        proj_root = ""
        rag_context_phase3 = ""
        
        # If user is asking for a new project/folder, don't inject existing RAG context!
        lower_in = user_input.lower()
        is_new_project = any(w in lower_in for w in ("new folder", "new project", "create a project", "create project"))
        
        try:
            engine = _get_engine()
            active = engine.get_active_project()
            if active and active.path:
                proj_root = active.path
                if not is_new_project:
                    rag_context_phase3 = engine.get_context(user_input)
                else:
                    console.print("[dim]New project requested. Omitting RAG context to prevent hallucinations.[/dim]")
        except Exception:
            pass
            
        if is_new_project and not proj_root:
            from core.paths import get_desktop_path
            proj_root = str(get_desktop_path())
            
        workflow_engine = WorkflowEngine()
        context = {
            "goal": user_input,
            "project_root": proj_root,
            "rag_context": rag_context_phase3
        }
        wf_id = workflow_engine.start_workflow(context)
        await workflow_engine.run_workflow(wf_id)
        return

    plan, code = "", ""

    # ── Inject RAG context if a project is active ───────────────────
    rag_context = ""
    try:
        engine = _get_engine()
        if engine.get_active_project_name():
            rag_context = engine.get_context(user_input)
            if rag_context:
                console.print(f"[dim]RAG context: {len(rag_context)} chars injected[/dim]")
    except Exception as exc:
        console.print(f"[dim]RAG context unavailable: {exc}[/dim]")

    if choice in ("1", "4"):
        console.print("\n[bold cyan]--- Planner ---[/bold cyan]")
        state.current_task = {"goal": user_input, "step": "Planning"}
        state.save()
        
        ctx = state.get_research_context()
        context_msg = f"Create a step-by-step plan for: {user_input}"
        if ctx:
            context_msg += f"\n\nResearch context:\n{ctx.to_prompt()}"
        if rag_context:
            context_msg += f"\n\nProject context:\n{rag_context}"
            
        plan = await agent_step("Planner", context_msg, memory)
        console.print(plan)
        if choice == "1":
            return
            
    if choice in ("2", "4"):
        console.print("\n[bold cyan]--- Edit Planner ---[/bold cyan]")
        if state.current_task:
            state.current_task["step"] = "Edit Planning"
        state.save()

        active = None
        try:
            engine = _get_engine()
            active = engine.get_active_project()
        except Exception:
            pass

        if active and active.path:
            from editing.coding_workflow import CodingWorkflow
            workflow = CodingWorkflow(project_root=active.path, rag_context=rag_context)
            result = await workflow.run(user_input, planner_hint=plan)
            code = result.message
            console.print(code)
            if choice == "2":
                return
            if choice == "4" and result.success:
                return
            if choice == "4" and not result.success:
                console.print(f"[yellow]{result.message}[/yellow]")
                console.print("[yellow]Falling back to Action Engine...[/yellow]")
        else:
            from editing.edit_planner import EditPlanner
            planner = EditPlanner()
            edit_plan = await planner.generate_plan(user_input, rag_context)
            console.print(f"[green]Edit Plan generated for: {edit_plan.goal}[/green]")
            code = str(edit_plan)
        if choice == "2":
            return
            
    if choice in ("3", "4"):
        console.print("\n[bold cyan]--- Runner ---[/bold cyan]")
        if not state.current_task:
            state.current_task = {"goal": user_input}
        state.current_task["step"] = "Executing"
        state.save()
        
        proj_path = ""
        try:
            active_proj = _get_engine().get_active_project()
            if active_proj:
                proj_path = active_proj.path
        except Exception:
            pass

        task = (
            f"Project: {proj_path}\nRequest: {user_input}\nPlan: {plan}\n\n"
            f"Apply all changes. Read each file before editing."
        )
        await execute_action(task, require_edits=is_coding_request(user_input))


# ── PROJECT route handler ───────────────────────────────────────────


async def auto_discover_and_open_project(user_input: str) -> None:
    """Auto-discover project from words and open it if found."""
    engine = _get_engine()
    state = AgentState()
    user_input = sanitize_text(user_input)
    cleaned, embedded_path = extract_open_path(user_input)
    
    if not embedded_path:
        from core.paths import get_desktop_path
        from pathlib import Path as P
        import difflib

        desktop = get_desktop_path()
        docs = P.home() / "Documents"
        
        dirs_to_check = []
        try:
            dirs_to_check.extend([d for d in desktop.iterdir() if d.is_dir()])
        except Exception as exc:
            logging.warning("Desktop scan failed (%s): %s", desktop, exc)
        try:
            dirs_to_check.extend([d for d in docs.iterdir() if d.is_dir()])
        except Exception as exc:
            logging.warning("Documents scan failed (%s): %s", docs, exc)
            
        dir_names_lower = [d.name.lower() for d in dirs_to_check]
        words = [w.strip("\"'.,!?") for w in cleaned.split()]
        
        for word in words:
            if not word or len(word) < 4:
                continue
            # Try exact/case-insensitive first
            cand = desktop / word
            if cand.is_dir():
                embedded_path = str(cand)
                break
            cand2 = docs / word
            if cand2.is_dir():
                embedded_path = str(cand2)
                break
                
            # Try fuzzy match
            matches = difflib.get_close_matches(word.lower(), dir_names_lower, n=1, cutoff=0.8)
            if matches:
                match_name = matches[0]
                for d in dirs_to_check:
                    if d.name.lower() == match_name:
                        embedded_path = str(d)
                        break
            if embedded_path:
                break

    if embedded_path:
        # Check if already active to avoid re-indexing/logging
        active = engine.get_active_project()
        if active and active.path and Path(active.path).resolve() == Path(embedded_path).resolve():
            return
            
        try:
            engine.open_project(embedded_path)
            state.active_project = Path(embedded_path).name
            state.save()
            console.print(f"[dim]Automatically opened project: {embedded_path}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Could not open {embedded_path}: {exc}[/yellow]")


async def handle_project_query(user_input: str, memory: str):
    """Handle project-related queries using the Knowledge Engine."""
    engine = _get_engine()
    state = AgentState()
    state.reload()
    user_input = sanitize_text(user_input)
    cleaned, embedded_path = extract_open_path(user_input)
    lower = cleaned.lower()
    active = engine.get_active_project()
    project_path = active.path if active else None

    # Skip slash-only commands handled by main loop
    if user_input.strip().lower().startswith("/") and not embedded_path:
        if not user_input.strip().lower().startswith("/open"):
            console.print("[yellow]Unknown command. Use /open, /memory, /projects[/yellow]")
        return

    # ── open project (natural language) ─────────────────────────────
    if "open" in lower and ("project" in lower or "my " in lower):
        words = user_input.split()
        path = None
        for word in words:
            from pathlib import Path as P
            candidate = P(word)
            if candidate.is_dir():
                path = str(candidate)
                break

        if path is None:
            active = engine.get_active_project()
            if active and active.path:
                path = active.path
                console.print(f"[dim]Restoring previous project: {active.name}[/dim]")
            else:
                console.print("[yellow]Please specify the project path: /open <path>[/yellow]")
                return

        console.print(f"[cyan]Opening project: {path}[/cyan]")
        try:
            info = engine.open_project(path)
            state.active_project = info.name
            state.save()
            console.print(f"[bold green]Project indexed![/bold green]")
            console.print(f"[blue]{info.summary()}[/blue]")
        except Exception as exc:
            console.print(f"[red]Failed to open project: {exc}[/red]")
        return

    if "remember" in lower and "project" in lower:
        if engine.remember_current_project():
            console.print("[green]Project saved to long-term memory.[/green]")
        else:
            console.print("[yellow]No active project to remember.[/yellow]")
        return

    # ── Deep-read + remember specific files ──────────────────────────
    wants_deep_read = any(
        p in lower
        for p in (
            "read this file",
            "read the file",
            "analyze this file",
            "analyze the file",
            "remember this file",
            "remember the file",
            "read and remember",
            "study this file",
        )
    )
    if wants_deep_read:
        from pathlib import Path as P
        candidates: list[P] = []
        for word in cleaned.replace("'", " ").replace('"', " ").split():
            cand = P(word.strip(",.;"))
            if cand.is_file():
                candidates.append(cand)
            elif project_path and not cand.is_absolute():
                joined = P(project_path) / cand
                if joined.is_file():
                    candidates.append(joined)
        if not candidates and project_path:
            # relative paths like src/app/layout.tsx
            for token in cleaned.replace("'", " ").replace('"', " ").split():
                if "/" in token or "\\" in token:
                    joined = P(project_path) / token.strip(",.;")
                    if joined.is_file():
                        candidates.append(joined)
        if not candidates:
            console.print(
                "[yellow]Tell me the file path to deep-read, e.g. "
                "`read and remember C:\\path\\to\\file.py`[/yellow]"
            )
            return
        for fp in candidates:
            result = engine.remember_file(fp, note=cleaned[:200])
            if result.get("status") == "success":
                console.print(
                    f"[green]Deep-read stored:[/green] {result['path']} "
                    f"({result['line_count']} lines)"
                )
            else:
                console.print(f"[red]{result.get('message')}[/red]")
        return

    # ── Run / start project (never use coding workflow) ─────────────
    is_run_request = is_run_project_request(cleaned)
    active = engine.get_active_project()
    project_path = active.path if active else None

    if is_run_request and not project_path:
        from pathlib import Path as P
        _, embedded = extract_open_path(cleaned)
        if embedded:
            project_path = embedded
        else:
            for word in cleaned.split():
                candidate = P(word.strip("\"'"))
                if candidate.is_absolute() and candidate.is_dir():
                    project_path = str(candidate)
                    break

    if is_run_request:
        if not project_path:
            console.print("[red]Open a project first: /open <path>[/red]")
            return
        try:
            engine.open_project(project_path)
            state.active_project = Path(project_path).name
            state.save()
        except Exception:
            pass
        await handle_run_project(project_path, cleaned)
        return

    # ── Coding workflow for edit/refactor/fix/debug requests ─────
    from editing.error_classifier import is_error_report

    runtime_error = is_error_report(cleaned) or (
        "react client manifest" in lower
        or "could not find the module" in lower
        or ("error type" in lower and "error message" in lower)
        or "manifest file is empty" in lower
        or ("failed to load resource" in lower and "404" in lower)
    )
    active = engine.get_active_project()
    project_path = active.path if active else None

    is_edit_request = (not is_run_request) and (
        runtime_error or is_coding_intent(cleaned)
    )

    if is_edit_request and not project_path:
        from pathlib import Path as P
        for word in cleaned.split():
            candidate = P(word.strip("\"'"))
            if candidate.is_absolute() and candidate.is_dir():
                project_path = str(candidate)
                try:
                    engine.open_project(project_path)
                    state.active_project = candidate.name
                    state.save()
                    console.print(f"[dim]Automatically opened project: {project_path}[/dim]")
                except Exception:
                    pass
                break

    if is_edit_request and not project_path:
        console.print(
            "[red]No project is currently open! I can't make coding changes until you open a project. "
            "Please use `/open <path>` first.[/red]"
        )
        return

    if project_path and is_edit_request:
        state.pending_coding_request = None
        if runtime_error:
            from editing.self_debug import SelfDebugOrchestrator

            result = await SelfDebugOrchestrator.run_from_error(
                cleaned, project_path, engine.get_context(cleaned)
            )
            if result.success:
                console.print(f"[bold green]{result.message}[/bold green]")
            else:
                console.print(f"[red]{result.message}[/red]")
            return
        await _run_coding_workflow(project_path, cleaned, engine)
        return

    # ── Project-scoped Q&A (retrieval + analysis) ───────────────────
    context = engine.get_context(cleaned)
    if context:
        console.print(f"[dim]Retrieved {len(context)} chars of project context[/dim]")

    response = await agent_step(
        "Project Analyst",
        cleaned,
        context or memory,
        _load_system_prompt(),
        persist_history=True,
    )
    console.print(Panel(Markdown(response), title="📊 Project Analyst", border_style="cyan"))
    await _speak_if_enabled(response)

    if _looks_like_actionable_plan(response):
        state.pending_coding_request = {"request": cleaned, "plan": response}
        state.save()
        console.print(
            "[dim]Reply [bold]yes[/bold] or [bold]make the changes[/bold] to implement this plan.[/dim]"
        )


# ── Main loop ───────────────────────────────────────────────────────


def _launch_immortility_hud() -> None:
    """Open the red JARVIS-style Immortility HUD in the browser."""
    try:
        from tools.hud_launcher import open_hud
        from tools.hud_state import update_hud

        # Non-blocking telemetry — never wait on cpu_percent(interval=…)
        try:
            import psutil

            update_hud(
                power=f"{min(99.9, 100 - psutil.cpu_percent(interval=0)):.1f}%",
                network=f"{min(99, int(psutil.virtual_memory().percent))}%",
                temp=f"{35 + psutil.cpu_percent(interval=0) / 4:.1f}C",
                tasks=4,
                status="ALL SUBSYSTEMS NOMINAL",
                sync="SYNCED",
            )
        except Exception:
            pass

        url = open_hud()
        console.print(f"[bold red]IMMORTILITY HUD[/bold red] → [link={url}]{url}[/link]")
    except Exception as exc:
        console.print(f"[yellow]HUD launch skipped: {exc}[/yellow]")


async def main():
    global _voice_enabled
    banner = Panel(
        Align.center(
            Text("IMMORTILITY\n", style="bold red", justify="center")
            .append("Your Local AI Assistant is Alive", style="italic bright_red")
        ),
        border_style="red",
        padding=(1, 4)
    )
    console.print(banner)
    try:
        from core.llm import active_backend

        console.print(f"[bold red]LLM:[/bold red] {active_backend()}")
    except Exception:
        pass
    _launch_immortility_hud()
    # Warm Gemini client in background (does not block the prompt)
    try:
        from core.llm import warm_llm
        import threading as _th

        _th.Thread(target=warm_llm, name="llm-warm-main", daemon=True).start()
    except Exception:
        pass
    memory = MemoryManager().get_full_summary() or ""
    console.print(f"[blue]Memory:[/blue] {memory[:120]}...\n" if memory else "")
    
    setup_registry()
    state = AgentState()
    state.cleanup_on_startup()
    console.print("[dim]State loaded.[/dim]")

    # ── Restore active project in background (don't block prompt) ───
    if state.active_project:
        proj_name = state.active_project

        def _bg_restore() -> None:
            try:
                engine = _get_engine()
                restored = engine.restore_project(proj_name)
                if restored:
                    console.print(
                        f"[dim]Restored project: {restored.name} "
                        f"({restored.total_chunks} chunks)[/dim]"
                    )
            except Exception as exc:
                console.print(f"[dim]Could not restore project: {exc}[/dim]")

        threading.Thread(target=_bg_restore, name="ke-restore", daemon=True).start()
        console.print(f"[dim]Restoring project '{proj_name}' in background…[/dim]")
    
    auto_mode = False
    voice_mode = False
    _voice_enabled = False

    completer = WordCompleter(
        ['/auto', '/talk', '/voice', '/listen', '/hud', '/clear', '/open', '/memory', '/import-docs', '/projects', '/exit'],
        ignore_case=True,
    )
    session = PromptSession(completer=completer)

    async def speak_reply(text: str) -> None:
        await _speak_if_enabled(text)

    async def listen_for_input() -> str | None:
        try:
            from tools.voice_io import get_voice

            console.print("[bold cyan]🎤 Listening… speak now (pause when done)[/bold cyan]")
            with console.status("[bold cyan]Recording + Whisper (offline)…[/bold cyan]", spinner="dots"):
                text = await asyncio.to_thread(get_voice().listen)
            text = (text or "").strip()
            if not text:
                console.print("[yellow]Heard nothing — try again.[/yellow]")
                return None
            console.print(f"[green]You said:[/green] {text}")
            return text
        except Exception as exc:
            console.print(f"[red]Voice listen failed: {exc}[/red]")
            console.print(
                "[dim]Tip: mic access + first Whisper model download (one-time) needed.[/dim]"
            )
            return None

    def get_bottom_toolbar():
        mode = "AUTO" if auto_mode else "NORMAL"
        voice = "ON" if voice_mode else "OFF"
        active_proj = state.active_project or "None"
        return HTML(
            f' <b>Mode:</b> <style bg="ansiblue"> {mode} </style> | '
            f'<b>Voice:</b> <style bg="ansigreen"> {voice} </style> | '
            f'<b>Project:</b> {active_proj} '
        )

    while True:
        try:
            hint = "speak or type" if voice_mode else "type"
            user = await session.prompt_async(
                HTML(f'<b><ansiyellow>&gt;</ansiyellow></b> <ansigray>({hint})</ansigray> '),
                bottom_toolbar=get_bottom_toolbar,
            )
        except EOFError:
            break
        except KeyboardInterrupt:
            continue

        user_stripped = user.strip()

        # Empty Enter in voice mode → listen from mic
        if not user_stripped and voice_mode:
            heard = await listen_for_input()
            if not heard:
                continue
            user_stripped = heard
        elif not user_stripped:
            continue
            
        # Queue input for background memory extraction (finally runs even on continue)
        _pending_auto_learn = user_stripped
        user_lower = user_stripped.lower()

        try:
            # Learn user name from casual introductions
            try:
                from memory.user_profile import UserProfile

                UserProfile().maybe_learn_from_text(user_stripped)
            except Exception:
                pass

            if user_lower in ("exit", "/exit"):
                break
                
            if user_lower == "/auto":
                auto_mode = not auto_mode
                console.print(f"[magenta]AUTO mode {'on' if auto_mode else 'off'}[/magenta]")
                continue
            
            if user_lower == "/hud":
                _launch_immortility_hud()
                continue

            if user_lower == "/talk":
                await run_speech_to_speech(memory)
                voice_mode = False
                _voice_enabled = False
                continue

            if user_lower == "/voice":
                # Alias: continuous speech-to-speech with local model only
                await run_speech_to_speech(memory)
                voice_mode = False
                _voice_enabled = False
                continue

            if user_lower == "/listen":
                heard = await listen_for_input()
                if not heard:
                    continue
                user_stripped = heard
                user_lower = user_stripped.lower()
                _pending_auto_learn = user_stripped
                _voice_enabled = True
                voice_mode = True
                # fall through into normal routing

            if user_lower == "/clear":
                state.conversation_history = []
                state.research_context = None
                state.pending_action = None
                state.current_task = None
                state.save()
                console.print("[green]State cleared.[/green]")
                continue
                
            # ── Phase 2 commands ────────────────────────────────────────
            if user_lower.startswith("/open"):
                lines = user_stripped.splitlines()
                first_line = lines[0]
                path = extract_open_path_only(first_line)
                if path:
                    try:
                        engine = _get_engine()
                        console.print(f"[cyan]Indexing project: {path}[/cyan]")
                        info = engine.open_project(path)
                        state.active_project = info.name
                        state.save()
                        console.print(f"[bold green]Project indexed![/bold green]")
                        console.print(f"[blue]{info.summary()}[/blue]")
                        follow_up = "\n".join(lines[1:]).strip()
                        if follow_up:
                            console.print(f"[dim]Continuing with: {follow_up[:80]}...[/dim]")
                            await handle_project_query(follow_up, memory)
                    except Exception as exc:
                        console.print(f"[red]Error: {exc}[/red]")
                else:
                    console.print("[yellow]Usage: /open <project-path>[/yellow]")
                continue

            if user_lower == "/memory":
                try:
                    engine = _get_engine()
                    stats = engine.stats()
                    console.print("[bold blue]Knowledge Engine Status[/bold blue]")
                    console.print(f"  Vector DB: {stats['vector_store']['total_chunks']} chunks")
                    console.print(f"  Docs DB: {stats['docs_store']['total_chunks']} chunks")
                    console.print(f"  Cache: {stats['cache']['size']} entries")
                    console.print(f"  Active Project: {stats['active_project'] or 'None'}")
                    console.print(f"  Remembered Projects: {', '.join(stats['remembered_projects']) or 'None'}")
                    console.print(f"  Conversations: {stats['conversation_entries']} entries")
                    console.print(f"  Preferences: {stats['preferences']} entries")
                except Exception as exc:
                    console.print(f"[red]Error: {exc}[/red]")
                continue

            if user_lower.startswith("/import-docs "):
                parts = user_stripped[13:].strip().split(" ", 1)
                if len(parts) == 2:
                    name, path = parts
                    try:
                        engine = _get_engine()
                        console.print(f"[cyan]Importing docs '{name}' from {path}...[/cyan]")
                        count = engine.import_docs(name, path)
                        console.print(f"[green]Imported {count} documentation chunks.[/green]")
                    except Exception as exc:
                        console.print(f"[red]Error: {exc}[/red]")
                else:
                    console.print("[yellow]Usage: /import-docs <name> <path>[/yellow]")
                continue

            if user_lower == "/projects":
                try:
                    engine = _get_engine()
                    projects = engine.recall("project")
                    if projects:
                        console.print("[bold blue]Remembered Projects:[/bold blue]")
                        for p in projects:
                            console.print(f"  • {p}")
                    else:
                        console.print("[dim]No projects remembered yet.[/dim]")
                except Exception as exc:
                    console.print(f"[red]Error: {exc}[/red]")
                continue

            if await handle_pending_coding_plan(user_stripped):
                continue

            if await handle_pending_action(user_stripped):
                continue

            # Fast path FIRST: desktop/projects scan — no RAG, no Knowledge Engine, no Gemini wait
            from core.desktop_scanner import spoken_projects_brief, wants_projects_scan

            if wants_projects_scan(user_stripped):
                console.print("[dim]Live filesystem scan (fast path)…[/dim]")
                await handle_desktop_projects_request(user_stripped)
                if _voice_enabled or voice_mode:
                    await _speak_if_enabled(spoken_projects_brief())
                continue

            # Fast conversational path: skip RAG/routing for short casual chat
            if _looks_like_fast_chat(user_stripped):
                from core.config import get_config

                state.mode = "CHAT"
                state.save()
                console.print("\n[bold blue][CHAT][/bold blue] [dim]fast[/dim]")
                with console.status("[bold cyan]🧠 Thinking...[/bold cyan]", spinner="bouncingBar"):
                    from core.llm import fast_chat

                    hist = [
                        {"role": m["role"], "content": m["content"]}
                        for m in (state.conversation_history or [])[-6:]
                        if m.get("role") in {"user", "assistant"} and m.get("content")
                    ]
                    response = await asyncio.to_thread(
                        fast_chat,
                        user_stripped,
                        history=hist,
                        max_output_tokens=get_config().cli_fast_chat_max_tokens,
                    )
                    state.append_message("user", user_stripped)
                    state.append_message("assistant", response)
                console.print(Panel(Markdown(response), title="🧠 Immortility", border_style="cyan"))
                await speak_reply(response)
                continue

            await auto_discover_and_open_project(user_stripped)

            try:
                console.print("[dim]Routing...[/dim]")

                if is_summarize_request(user_stripped):
                    state.mode = "ACTION"
                    state.save()
                    with console.status("[bold cyan]📖 Summarizing...[/bold cyan]", spinner="bouncingBar"):
                        summary = await handle_summarize_webpage()
                    console.print(Panel(Markdown(summary), title="📝 Summary", border_style="cyan"))
                    await speak_reply(summary)
                    continue

                if is_page_query(user_stripped):
                    registry = ToolRegistry()
                    registry.setup()
                    result = await registry.execute("get_current_url", {})
                    data = json.loads(result)
                    url = data.get("result", {}).get("url", "No page open")
                    console.print(f"\n[bold blue]Current page:[/bold blue] {url}")
                    state.append_message("assistant", f"You are on: {url}")
                    continue

                with console.status("[bold cyan]🔄 Routing...[/bold cyan]", spinner="dots"):
                    engine = _get_engine()
                    # Context-first orchestration: retrieve once before intent routing.
                    orchestrated_context = engine.get_routing_context(user_stripped)
                    route = await classify_route(user_stripped, routing_context=orchestrated_context)

                if route == "CHAT":
                    from core.config import get_config

                    state.mode = "CHAT"
                    state.save()
                    console.print("\n[bold blue][CHAT][/bold blue]")
                    with console.status("[bold cyan]🧠 Thinking...[/bold cyan]", spinner="bouncingBar"):
                        from core.llm import fast_chat

                        # Keep replies snappy — skip huge system prompt + long RAG dump
                        extra = ""
                        if orchestrated_context:
                            extra = orchestrated_context[:1800]
                        elif memory:
                            extra = memory[:1200]
                        hist = [
                            {"role": m["role"], "content": m["content"]}
                            for m in (state.conversation_history or [])[-6:]
                            if m.get("role") in {"user", "assistant"} and m.get("content")
                        ]
                        response = await asyncio.to_thread(
                            fast_chat,
                            user_stripped,
                            history=hist,
                            extra_context=extra,
                            max_output_tokens=get_config().cli_chat_max_tokens,
                        )
                        state.append_message("user", user_stripped)
                        state.append_message("assistant", response)
                    console.print(Panel(Markdown(response), title="🧠 Immortility", border_style="cyan"))
                    await speak_reply(response)

                elif route == "ACTION":
                    state.mode = "ACTION"
                    state.save()
                    if is_run_project_request(user_stripped):
                        engine = _get_engine()
                        active = engine.get_active_project()
                        path = active.path if active else None
                        if not path:
                            _, path = extract_open_path(user_stripped)
                        if path:
                            await handle_run_project(path, user_stripped)
                        else:
                            console.print("[red]Open a project first: /open <path>[/red]")
                    else:
                        action_context = ""
                        try:
                            # Action context extends the initial orchestration with multi-hop graph context.
                            action_context = _get_engine().get_action_context(user_stripped)
                        except Exception:
                            action_context = ""
                        await execute_action(user_stripped, context_override=action_context)

                elif route == "PROJECT":
                    state.mode = "PROJECT"
                    state.save()
                    console.print("\n[bold blue][PROJECT][/bold blue]")
                    await handle_project_query(user_stripped, memory)

                elif route in ("TASK", "RESEARCH_TASK"):
                    state.mode = route
                    state.save()
                    is_research = route == "RESEARCH_TASK"

                    if auto_mode or (is_research and "leetcode" in user_lower):
                        await execute_workflow(user_stripped, memory, "4", is_research)
                    elif is_coding_request(user_stripped):
                        await execute_workflow(user_stripped, memory, "2", is_research)
                    else:
                        console.print(f"\n[bold yellow][{route}][/bold yellow]")
                        console.print("1. Plan  2. Code  3. Execute  4. Full Workflow")
                        choice = await asyncio.to_thread(
                            console.input, "[bold yellow]Choice (1-4): [/bold yellow]"
                        )
                        if choice in ("1", "2", "3", "4"):
                            await execute_workflow(user_stripped, memory, choice, is_research)
                        else:
                            console.print("[red]Invalid choice.[/red]")

            except asyncio.CancelledError:
                console.print("\n[red]Cancelled.[/red]")
                state.pending_action = None
                state.save()
            except KeyboardInterrupt:
                console.print("\n[red]Interrupted.[/red]")
                break
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/red]")
                logging.exception("Main loop error")
        finally:
            # Auto-learn runs even when a branch `continue`s (Python runs finally first)
            if _pending_auto_learn:
                try:
                    engine = _get_engine()
                    await engine._memory.auto_learn(_pending_auto_learn)
                except Exception:
                    pass
                _pending_auto_learn = None

    # ── Shutdown ────────────────────────────────────────────────────
    console.print("[dim]Cleaning up resources and stopping background tasks...[/dim]")
    if _knowledge_engine is not None:
        try:
            _knowledge_engine.shutdown()
        except Exception:
            pass
            
    try:
        await BrowserManager().close()
    except Exception:
        pass
    try:
        from tools.hud_launcher import stop_hud_server

        stop_hud_server()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
