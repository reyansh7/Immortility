"""HUD primary agent — execute what Reyansh asks in the Immortility UI.

Opens Chrome (user profile), apps, files, and coding actions — not chat-only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Re-export site map for any callers; canonical logic lives in user_browser
from tools.user_browser import SITE_HOME as _SITE_MAP  # noqa: E402

_APP_ALIASES = {
    "chrome": "chrome",
    "brave": "brave",
    "vscode": "vscode",
    "vs code": "vscode",
    "code": "vscode",
    "notepad": "notepad",
    "explorer": "explorer",
    "file explorer": "explorer",
    "terminal": "terminal",
    "powershell": "powershell",
    "whatsapp": "whatsapp",
}

_ACTION_HINTS = (
    "open ", "launch ", "run ", "create ", "delete ", "write ",
    "edit ", "fix ", "implement ", "refactor ", "install ", "download ",
    "make a ", "make the ", "add ", "remove ", "rename ", "build ",
    "kill ", "close ", "go to ", "navigate ", "browse ",
    "analyze ", "analyse ", "review ", "look at ", "check ",
    "read ", "list ", "inspect ", "summarize ", "summarise ",
    "start server", "start the server", "start the app", "start npm",
    "start uvicorn", "start my ",
)

_CHAT_ONLY = frozenset({
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "cool",
    "good morning", "good night", "how are you", "who are you",
})

_ANALYSIS_HINTS = (
    "any bug", "any bugs", "is there a bug", "find bugs", "look for bugs",
    "tell me if", "analyze", "analyse", "review the", "review this",
    "what's wrong", "what is wrong", "explain the code", "what does this",
    "check for bugs", "find issues", "any issues", "code review",
)

_EDIT_HINTS = (
    "fix ", "fix the", "implement", "refactor", "edit the", "edit file",
    "edit ", "create file", "create a file", "write code", "make the changes",
    "make changes", "apply the", "patch ", "delete file", "rename ",
    "add a ", "remove the", "beautify", "beautif", "improve the face",
    "improve your face", "update the face", "change the face", "redesign",
    "modify the", "modify ", "update the code", "change the code",
    "write_file", "can you edit",
)


def _wants_edits(message: str) -> bool:
    """True only for explicit edit/fix intent — not bare word 'code'."""
    low = message.lower()
    if _is_analysis_request(message) and not any(
        h in low for h in (
            "fix ", "implement", "refactor", "make the changes", "make changes",
            "beautify", "edit ", "improve", "update the face", "change the face",
        )
    ):
        return False
    return any(h in low for h in _EDIT_HINTS)


def _wants_face_beautify(message: str) -> bool:
    low = (message or "").lower()
    if not low:
        return False
    faceish = any(w in low for w in ("face", "hud", "frontend", "immortility_hud", "html"))
    beautify = any(
        w in low
        for w in (
            "beautify", "beautif", "prettier", "human", "less scary",
            "improve", "redesign", "make changes", "edit the file",
            "update the face", "change the face", "can you edit",
        )
    )
    return faceish and beautify


def apply_human_face_beautify() -> str:
    """Jarvis metallic face lives in frontend/immortility_hud.html — do not overwrite."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "frontend" / "immortility_hud.html"
    if not path.is_file():
        return f"HUD file missing: {path}"

    html = path.read_text(encoding="utf-8")
    if 'id="skinBase"' in html and 'class="circuits"' in html:
        return (
            "Face already uses the Jarvis metallic redesign "
            "(human jaw, crimson circuits, glowing eyes). "
            "Hard-refresh the HUD (Ctrl+Shift+R) if you don't see it."
        )
    return (
        "Open frontend/immortility_hud.html — apply the Jarvis metallic SVG there."
    )


def _normalize_token(tok: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", tok.lower()).strip()


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"']+", text, flags=re.I)


def _extract_sites(text: str) -> list[str]:
    """Find known site names mentioned after open/launch/go to."""
    low = text.lower()
    found: list[str] = []
    # Prefer longer aliases first
    for name in sorted(_SITE_MAP.keys(), key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            url = _SITE_MAP[name]
            if url not in found:
                found.append(url)
    return found


def _extract_apps(text: str) -> list[str]:
    low = text.lower()
    apps: list[str] = []
    for alias, app in sorted(_APP_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
            if app not in apps:
                apps.append(app)
    return apps


def try_open_browsers(message: str) -> str | None:
    """Open sites/searches in Reyansh's logged-in Chrome profile."""
    from tools.user_browser import handle_browser_request

    return handle_browser_request(message)

def try_open_apps(message: str) -> str | None:
    low = message.lower().strip()
    if not any(w in low for w in ("open ", "launch ", "start ")):
        return None
    # If it's mainly websites, skip apps (chrome alone is ok)
    sites = _extract_sites(message)
    apps = _extract_apps(message)
    if sites and not apps:
        return None
    # Don't treat "open google" as opening chrome — that's a site
    if "google" in low and "chrome" not in low and not apps:
        return None

    apps = [a for a in apps if a != "chrome" or "chrome" in low]
    # "open SkillLens code" / analysis → open the project folder, not blank VS Code
    if "vscode" in apps and (
        _is_analysis_request(message)
        or re.search(r"\b[\w.-]+\s+code\b", low)
        or "skill lens" in low
        or "skilllens" in low
    ):
        apps = [a for a in apps if a != "vscode"]
    if not apps:
        return None

    from tools.app_tool import AppTool

    results = []
    for app in apps:
        res = AppTool.open_application(app)
        results.append(res.get("message") or str(res))
    return " ".join(results)


def _is_analysis_request(message: str) -> bool:
    low = message.lower()
    return any(h in low for h in _ANALYSIS_HINTS)


def _try_open_explicit_folder_path(message: str) -> str | None:
    """Open an explicit Windows folder path exactly as requested."""
    low = (message or "").lower()
    if not any(w in low for w in ("open ", "launch ", "show ")):
        return None
    m = re.search(r"[A-Za-z]:\\[^\s\"']+", message)
    if not m:
        return None
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    p = Path(m.group(0))
    if not p.is_dir():
        return None
    code_bin = shutil.which("code")
    try:
        if code_bin:
            if sys.platform == "win32":
                subprocess.Popen(f'code "{p}"', shell=True)
            else:
                subprocess.Popen([code_bin, str(p)])
            return f"Opened `{p}` in VS Code."
        subprocess.Popen(
            ["explorer", str(p)] if sys.platform == "win32" else ["xdg-open", str(p)]
        )
        return f"Opened `{p}` in Explorer."
    except Exception:
        return None


def _try_scan_single_folder(message: str) -> str | None:
    """Deterministic scan for one folder using only live filesystem data."""
    low = (message or "").lower()
    if not re.search(r"\b(scan|analy[sz]e|inspect|review|tell me about|about)\b", low):
        return None
    if not any(w in low for w in ("folder", "desktop", "project", "number ", "no.", "#")):
        return None
    try:
        from core.desktop_scanner import format_single_folder_report, resolve_scan_target

        target = resolve_scan_target(message)
        if target is None:
            return None
        return format_single_folder_report(target)
    except Exception:
        return None


def _try_open_project_folder(message: str) -> str | None:
    """Open a Desktop project folder in VS Code / Explorer when asked."""
    low = message.lower()
    # Explicit paths are handled by _try_open_explicit_folder_path.
    if re.search(r"[A-Za-z]:\\[^\s\"']+", message):
        return None
    # Never steal /open indexing or "index/read projects" into VS Code
    if low.strip().startswith("/open"):
        return None
    try:
        from tools.hud_knowledge import wants_index_or_ingest

        if wants_index_or_ingest(message):
            return None
    except Exception:
        pass
    if re.search(r"\b(index|ingest|reindex|embed)\b", low):
        return None
    if not any(w in low for w in ("open ", "launch ", "show ", "analyze", "analyse", "review", "check")):
        return None

    # Never steal "open youtube / google / it in chrome" into VS Code
    try:
        from tools.user_browser import wants_browser_action

        if wants_browser_action(message):
            return None
    except Exception:
        pass
    if re.search(r"\b(youtube|netflix|google|chrome|browser|wikipedia|leetcode)\b", low):
        if not re.search(r"\b(folder|project|codebase|repo|skilllens)\b", low):
            return None

    from core.paths import get_desktop_path

    desktop = get_desktop_path()
    # Known aliases → folder names on Desktop
    aliases = {
        "skilllens": "SkillLens",
        "skill lens": "SkillLens",
        "talentlens": "SkillLens",
        "immortility": "immortility1",
    }
    folder_name = None
    for alias, name in aliases.items():
        if alias in low:
            folder_name = name
            break
    if not folder_name:
        # "open MyProject code" / "open MyProject folder"
        m = re.search(
            r"\b(?:open|launch|analyze|analyse|review|check)\s+([a-z0-9._\- ]+?)(?:\s+code|\s+folder|\s+project)?\b",
            low,
        )
        if m:
            cand = m.group(1).strip()
            skip = {
                "youtube", "chrome", "the", "my", "a", "it", "this", "that",
                "them", "google", "netflix", "browser", "wikipedia", "leetcode",
            }
            if cand not in skip and not any(s in cand for s in skip):
                # try exact / fuzzy on Desktop
                for p in desktop.iterdir() if desktop.is_dir() else []:
                    if p.is_dir() and p.name.lower().replace(" ", "") == cand.replace(" ", ""):
                        folder_name = p.name
                        break
                    if p.is_dir() and cand in p.name.lower():
                        folder_name = p.name
                        break

    if not folder_name:
        return None

    path = desktop / folder_name
    if not path.is_dir():
        # also try Desktop\Projects\
        alt = desktop / "Projects" / folder_name
        if alt.is_dir():
            path = alt
        else:
            return None

    # Prefer VS Code; fall back to Explorer
    import subprocess
    import shutil
    import sys

    try:
        code_bin = shutil.which("code")
        if code_bin:
            # On Windows, `code` resolves to code.CMD — needs shell
            if sys.platform == "win32":
                subprocess.Popen(f'code "{path}"', shell=True)
            else:
                subprocess.Popen([code_bin, str(path)])
            return f"Opened {path} in VS Code."
        subprocess.Popen(["explorer", str(path)] if sys.platform == "win32" else ["xdg-open", str(path)])
        return f"Opened {path} in Explorer."
    except Exception as exc:
        logger.debug("open project folder failed: %s", exc)
        try:
            subprocess.Popen(["explorer", str(path)])
            return f"Opened {path} in Explorer."
        except Exception:
            return None


def _wants_action(message: str) -> bool:
    low = message.lower().strip()
    if low in _CHAT_ONLY:
        return False
    if _extract_urls(message):
        return True
    if any(h in low for h in _ACTION_HINTS):
        return True
    if any(
        k in low
        for k in (
            "file", "folder", "directory", "code", "bug", "error",
            "function", "class", "project", "script", "python",
            "javascript", "typescript", "html", "css",
        )
    ) and any(
        k in low
        for k in ("create", "delete", "write", "edit", "fix", "make", "add", "remove", "change")
    ):
        return True
    return False


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Nested — run in a fresh thread with its own loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def handle_hud_request(
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Process a HUD chat/voice message and actually do the work."""
    message = (message or "").strip()
    if not message:
        return "Say something and I'll handle it, Reyansh."

    try:
        from memory.outcome_memory import get_outcome_memory, outcome_learning_enabled

        if outcome_learning_enabled():
            get_outcome_memory().score_and_record_previous(message)
    except Exception:
        pass

    multi_prefix = ""

    # Pending confirmation from a previous HUD/CLI action
    try:
        from core.agent_state import AgentState
        from core.pending_action import is_approval, is_rejection

        state = AgentState()
        if state.pending_action:
            if is_approval(message):
                pending = state.pending_action
                state.pending_action = None
                state.save()

                async def _resume() -> str:
                    from core.action_engine import resume_confirmed_pending

                    try:
                        from rich.console import Console

                        Console().print(
                            f"[bold yellow][HUD confirm][/bold yellow] "
                            f"Approved {pending.get('tool')}"
                        )
                    except Exception:
                        pass
                    return await resume_confirmed_pending(pending)

                return _run_async(_resume()) or "Done."
            if is_rejection(message):
                state.pending_action = None
                state.save()
                return "Cancelled."
    except Exception as exc:
        logger.debug("pending check: %s", exc)

    # Knowledge: /open indexing + bulk desktop ingest + "read the projects"
    # MUST run before projects-scan / VS Code folder open (those used to steal these).
    try:
        from tools.hud_knowledge import handle_hud_knowledge, wants_index_or_ingest

        if wants_index_or_ingest(message) or message.lower().startswith("/open"):
            from rich.console import Console

            Console().print(
                f"[bold cyan][HUD → Knowledge/Index][/bold cyan] {message[:140]}"
            )
            reply = handle_hud_knowledge(message)
            if reply:
                return (multi_prefix + reply).strip() if multi_prefix else reply
    except Exception as exc:
        logger.exception("HUD knowledge/index failed")
        return f"Indexing failed: {exc}"

    # Face / HUD beautify — deterministic edit (local 8B fails on 1000-line HTML)
    if _wants_face_beautify(message) or (
        _wants_edits(message)
        and any(w in message.lower() for w in ("face", "hud", "immortility_hud", "frontend"))
    ):
        try:
            from rich.console import Console

            Console().print(
                f"[bold magenta][HUD → Face Beautify][/bold magenta] {message[:120]}"
            )
        except Exception:
            pass
        return apply_human_face_beautify()

    # Multi-action fast path — "create folder X and open youtube" in ONE prompt
    try:
        from tools.multi_actions import multi_actions_complete, run_multi_actions

        multi_reply = run_multi_actions(message)
        if multi_reply and multi_actions_complete(message, multi_reply):
            try:
                from rich.console import Console

                Console().print(
                    f"[bold green][HUD → Multi][/bold green] {message[:120]}"
                )
            except Exception:
                pass
            return multi_reply
        # Partial multi (e.g. folder done, still need coding) — keep reply as prefix later
        multi_prefix = (multi_reply + "\n") if multi_reply else ""
    except Exception as exc:
        logger.debug("multi-actions: %s", exc)
        multi_prefix = ""

    # LeetCode — model solves + saves to Desktop (before projects scan steals "desktop")
    try:
        from tools.leetcode_tool import wants_leetcode

        if wants_leetcode(message):
            from rich.console import Console

            Console().print(
                f"[bold cyan][HUD → LeetCode Skill][/bold cyan] {message[:120]}"
            )

            async def _lc() -> str:
                from skills.leetcode_skill import LeetCodeSkill

                return await LeetCodeSkill().run(message)

            return (multi_prefix + (_run_async(_lc()) or "LeetCode workflow finished.")).strip()
    except Exception as exc:
        logger.exception("LeetCode HUD path failed")
        return f"LeetCode workflow error: {exc}"

    # (folder + browser already handled above via multi_actions)

    # Single-folder deterministic scan (no inferred/fake files).
    one_scan = _try_scan_single_folder(message)
    if one_scan:
        return (multi_prefix + one_scan).strip()

    # Desktop / projects listing — natural answer + learn into TurboVec
    try:
        from core.desktop_scanner import (
            answer_desktop_projects_query,
            wants_projects_scan,
        )

        if wants_projects_scan(message):
            return (multi_prefix + answer_desktop_projects_query(message, learn=True)).strip()
    except Exception:
        pass

    # Shared source router — WEB only when policy says so (with RAG context)
    try:
        from core.source_router import choose_source

        routing_ctx = ""
        try:
            from knowledge.engine import KnowledgeEngine

            # Cheap memory-aware signal so we don't web-search when RAG already knows
            routing_ctx = KnowledgeEngine().get_routing_context(message, n_results=4) or ""
        except Exception:
            routing_ctx = ""

        _src = choose_source(message, routing_context=routing_ctx)
        if _src.source == "WEB":
            from agents.research_agent import ResearchAgent
            from core.reply_format import polish_reply
            from memory.outcome_memory import get_outcome_memory

            def _run_research() -> str:
                import asyncio

                async def _go() -> str:
                    ctx = await ResearchAgent().execute(message)
                    # Prefer agent synthesis; lightly polish
                    body = (ctx.summary or ctx.extracted_text or "").strip()
                    if not body:
                        return "Web research returned no usable sources."
                    return polish_reply(body)

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(lambda: asyncio.run(_go())).result(timeout=180)
                return asyncio.run(_go())

            reply = (multi_prefix + _run_research()).strip()
            try:
                from core.critic import apply_critic_or_retry
                import re as _re

                urls = _re.findall(r"https?://[^\s\])>]+", reply)
                reply = apply_critic_or_retry(message, reply, sources=urls, mode="WEB")
            except Exception:
                pass
            try:
                get_outcome_memory().set_pending(message, "WEB", reply)
            except Exception:
                pass
            return reply
    except Exception as exc:
        logger.exception("HUD web research failed")
        return f"Web research failed: {exc}"

    # Explicit open / index already handled above; continue ladder


    # Index Immortility into local TurboVec (store code for RAG)
    low_msg = message.lower().strip()
    if re.search(
        r"\b(index|ingest|remember)\b.*\b(immortility|yourself|your\s+code|own\s+code)\b",
        low_msg,
    ) or low_msg in {"index immortility", "index yourself", "remember your code"}:
        try:
            from tools.self_inspect import try_index_self

            info = try_index_self()
            stats = info.get("stats") or {}
            vs = stats.get("vector_store") or {}
            return (
                f"Indexed Immortility into the local vector DB.\n"
                f"- Path: {info.get('path')}\n"
                f"- Code chunks now: {vs.get('total_chunks', '?')}\n"
                f"Ask me to read knowledge or analyze your vector database anytime."
            )
        except Exception as exc:
            logger.exception("self-index failed")
            return f"Could not index Immortility yet: {exc}"

    # Own RAG / knowledge / vector DB — read disk, never web-search
    try:
        from tools.self_inspect import handle_self_inspect, wants_self_inspect

        if wants_self_inspect(message):
            return handle_self_inspect(message)
    except Exception as exc:
        logger.exception("self-inspect failed")
        return f"I hit an error reading my local knowledge systems: {exc}"

    # Default browser opens (YouTube, Netflix, LeetCode, …)
    # Skip if multi-actions already opened them
    if multi_prefix and ("Opened in your" in multi_prefix or "Chrome profile" in multi_prefix):
        browser_reply = None
    else:
        browser_reply = try_open_browsers(message)
    app_reply = try_open_apps(message)

    # If we opened browsers/apps and the request was only that — done
    # NOTE: use word boundaries — "leetcode" must NOT match the token "code"
    only_open = bool(browser_reply or app_reply) and not re.search(
        r"\b(create|delete|write|edit|fix|implement|refactor|install)\b",
        message.lower(),
    ) and not re.search(r"\b(code|file|folder)\b", message.lower())
    if only_open:
        parts = [p for p in (browser_reply, app_reply) if p]
        return (multi_prefix + " ".join(parts)).strip()

    explicit_open = _try_open_explicit_folder_path(message)
    if explicit_open:
        return (multi_prefix + explicit_open).strip()

    # Open a known Desktop project folder (SkillLens, etc.)
    project_open = _try_open_project_folder(message)

    # Full agent for files / coding / complex actions
    # Advisory follow-ups ("how do I start the project?") stay in chat with history —
    # never route them to the action engine (which web-searches and dumps Medium links).
    from core.chat_thread import is_advisory_chat

    advisory = is_advisory_chat(message)
    use_action = (
        not advisory
        and (
            _wants_action(message)
            or (browser_reply and not only_open)
            or _is_analysis_request(message)
        )
    )
    if use_action:
        prefix = multi_prefix
        if browser_reply:
            prefix += browser_reply + "\n"
        if app_reply:
            prefix += app_reply + "\n"
        if project_open:
            prefix += project_open + "\n"
        try:
            from core.action_engine import execute_action
            from core.router import _fast_route

            route = _fast_route(message) or "ACTION"
            # Analysis / bug-hunt = read-only. Never force edits because of the word "code".
            require_edits = _wants_edits(message) or (
                route == "PROJECT" and _wants_edits(message)
            )

            context_override = ""
            try:
                from knowledge.engine import KnowledgeEngine

                context_override = KnowledgeEngine().get_action_context(
                    message, n_results=8
                ) or ""
                if not context_override.strip():
                    context_override = (
                        "RAG store has little/no indexed context for this query. "
                        "Prefer list_directory/read_file on absolute paths under "
                        "the Immortility repo or the active project. "
                        "Tell Reyansh to say 'index immortility' if self-code RAG is empty."
                    )
            except Exception as exc:
                logger.debug("action context: %s", exc)

            try:
                from rich.console import Console

                Console().print(
                    f"[bold magenta][HUD → Action Engine][/bold magenta] {message[:160]}"
                )
            except Exception:
                pass

            async def _act() -> str:
                return await execute_action(
                    message,
                    require_edits=require_edits,
                    auto_confirm=False,
                    context_override=context_override,
                )

            result = _run_async(_act())
            from core.reply_format import polish_reply

            return (prefix + polish_reply(result or "Done.")).strip()
        except Exception as exc:
            logger.exception("HUD action failed")
            if prefix:
                return f"{prefix}Action engine error: {exc}"
            return f"I hit an error doing that: {exc}"

    if project_open:
        return project_open

    # Conversational reply (with local memory + optional RAG snippets)
    from core.chat_thread import (
        focus_context_for_message,
        history_for_model,
        update_focus_after_turn,
    )
    from core.llm import fast_chat
    from core.project_resolve import (
        known_project_names,
        project_path_for_name,
        resolve_project_from_query,
    )
    from core.reply_format import REPLY_FORMAT_RULES
    from tools.self_inspect import project_root

    root = project_root()
    rag_bits = ""
    memory_bits = ""
    mentioned = resolve_project_from_query(message)
    project_note = ""
    thread_note = focus_context_for_message(message)

    try:
        from knowledge.engine import KnowledgeEngine

        ke = KnowledgeEngine()
        if mentioned:
            try:
                from core.agent_state import AgentState

                st = AgentState()
                st.active_project = mentioned
                st.save()
            except Exception:
                pass
            chunks = 0
            try:
                chunks = ke._vector_store.count_by_project(mentioned)  # noqa: SLF001
            except Exception:
                chunks = 0
            ppath = project_path_for_name(mentioned)
            if chunks <= 0:
                where = str(ppath) if ppath else mentioned
                return (
                    f"I know you mean {mentioned} "
                    f"({where}), but it is not in TurboVec yet "
                    f"(0 chunks — likely a broken earlier index pass).\n\n"
                    f"Say `/open {where}` or index {mentioned} and I will "
                    f"re-embed it, then ask me again."
                )
            project_note = (
                f"Reyansh is asking about project {mentioned} "
                f"({ppath or 'Desktop/Projects'}), which has {chunks} TurboVec chunks. "
                f"Answer from retrieved code context about THIS project only — "
                f"not a generic stock-trading app."
            )
            rag_bits = (
                ke.get_routing_context(message, n_results=8) or ""
            ).strip()
        else:
            # Avoid stuffing unrelated RAG when continuing a new project-idea thread
            if not thread_note:
                rag_bits = (ke.get_routing_context(message, n_results=4) or "").strip()
        try:
            memory_bits = (ke._memory.get_full_summary(  # noqa: SLF001
                mentioned or ke.get_active_project_name() or ""
            ) or "").strip()
        except Exception:
            memory_bits = ""
    except Exception:
        pass

    known = ", ".join(known_project_names()[:24])
    system = (
        "You are Immortility, Reyansh's desktop AI running locally. "
        f"Your codebase root is `{root}`. "
        f"RAG lives in `{root / 'rag'}`, orchestration in `{root / 'knowledge'}`, "
        f"TurboVec DB in `{root / '.vector_db'}`. "
        "Backend actions run in the Immortility terminal. "
        "You CAN read local files and index Desktop projects into TurboVec via the HUD. "
        "You CAN open sites and searches in Reyansh's Chrome (YouTube, Google, Netflix, etc.). "
        "Never say you cannot open YouTube or the browser — the HUD does that for real. "
        "When he names a Desktop project (e.g. 'stocks app' = stocks_app), treat it as "
        "THAT codebase — summarize from retrieved local context, never invent a generic app. "
        f"Known projects: {known}. "
        "Conversation continuity is critical: if he refers to 'the project', 'it', 'this', "
        "or asks how to start something you already discussed in this chat, CONTINUE that "
        "same idea with concrete next steps. Do not web-search. Do not invent a list of "
        "Medium articles. Prefer the plan already in the chat history / ongoing topic. "
        "If he asks to index/read projects and it wasn't done yet, tell him to say "
        "'index my desktop projects' or `/open <path>` — the HUD runs real ingest. "
        "Never say you cannot access files or invent Microsoft/Medium web links "
        "when asked about YOUR vector DB / RAG / knowledge folder. "
        "Address him as Reyansh. For voice, keep answers shorter. "
        f"{REPLY_FORMAT_RULES}"
    )
    if thread_note:
        system += f"\n\n{thread_note}"
    if project_note:
        system += f"\n\n{project_note}"
    if memory_bits:
        system += f"\n\nMemory pack:\n{memory_bits[:1600]}"
    if rag_bits and not thread_note:
        system += f"\n\nRetrieved local code context:\n{rag_bits[:3500]}"
    elif mentioned:
        system += (
            f"\n\nNo code snippets retrieved for `{mentioned}` — say you need a "
            f"re-index rather than inventing features."
        )
    try:
        from memory.outcome_memory import lessons_for_prompt

        lessons = lessons_for_prompt(message)
        if lessons:
            system += f"\n\n{lessons}"
    except Exception:
        pass

    hist = history_for_model(history, max_turns=16)
    reply = fast_chat(
        message,
        history=hist,
        system=system,
        max_output_tokens=520,
    )
    try:
        update_focus_after_turn(message, reply, hist)
    except Exception:
        pass
    try:
        from memory.outcome_memory import get_outcome_memory

        get_outcome_memory().set_pending(message, "CHAT", reply)
    except Exception:
        pass
    return reply
