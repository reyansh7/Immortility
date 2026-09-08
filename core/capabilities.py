"""Deterministic capability registry — never LLM memory.

Compact ``capability_card()`` is prompt-safe. Detailed ``capability_report()``
is for /capabilities and /doctor.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.router import capability_available, model_id_for_role, select_for_role
from models.types import (
    CAP_ASR,
    CAP_CHAT,
    CAP_CODE,
    CAP_EMBED,
    CAP_RERANK,
    CAP_TTS,
    CAP_VISION,
)

# Hardcoded. Confirmation, env, and config cannot flip these on.
UNSUPPORTED: dict[str, str] = {
    "wifi_password_bypass": "Intentionally not supported.",
    "packet_injection": "Intentionally not supported.",
    "deauth_attack": "Intentionally not supported.",
    "credential_theft": "Intentionally not supported.",
}

_CARD_LABELS = (
    ("chat", "CHAT"),
    ("code", "CODE"),
    ("vision", "VISION"),
    ("asr", "ASR"),
    ("tts", "TTS"),
    ("embed", "EMBED"),
    ("rerank", "RERANK"),
    ("local_files", "LOCAL FILES"),
    ("terminal", "TERMINAL"),
    ("browser", "BROWSER"),
    ("web_search", "WEB SEARCH"),
    ("inspect_url", "URL INSPECTION"),
    ("rag", "RAG"),
    ("project_index", "PROJECT INDEX"),
    ("git", "GIT"),
    ("documents", "DOCUMENTS"),
    ("docker", "DOCKER"),
    ("database", "DATABASE"),
)


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    network: bool = False
    requires_confirmation: bool = False
    source: str = ""
    detail: str = ""


def _tool_names() -> set[str]:
    try:
        from tools.tool_registry import ToolRegistry

        registry = ToolRegistry()
        registry.setup()
        return set(registry.list_tools())
    except Exception:
        return set()


def _has_tool(*names: str) -> bool:
    have = _tool_names()
    return any(name in have for name in names)


def _which(name: str) -> bool:
    import shutil

    return shutil.which(name) is not None


def _document_parsers_ok() -> bool:
    try:
        import fitz  # noqa: F401
        import docx  # noqa: F401
        import pptx  # noqa: F401
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def _document_detail() -> str:
    if not _has_tool("extract_document"):
        return "extract_document not registered"
    if not _document_parsers_ok():
        return "parsers missing (pymupdf/python-docx/python-pptx/openpyxl)"
    return "PDF/DOC/DOCX/PPTX/XLSX/CSV parsers"


def _database_detail() -> str:
    if not _has_tool("db_list_connections"):
        return "not registered"
    try:
        from tools.database_tool import load_connections

        n = len(load_connections())
    except Exception:
        n = 0
    return f"{n} configured connection(s); named connections only"


def _model_cap(name: str, cap: str, role: str) -> Capability:
    selection = select_for_role(role)
    available = capability_available(cap)
    model = model_id_for_role(role)
    if name == "vision" and not available:
        return Capability(
            name=name,
            available=False,
            source="models.router",
            detail="unavailable - set OLLAMA_VISION_MODEL; Qwythos cannot see images",
        )
    if not available:
        return Capability(
            name=name,
            available=False,
            source="models.router",
            detail=f"no model for role {role}",
        )
    extra = ""
    if selection and selection.degraded and name == "code":
        extra = " (brain fallback)"
    return Capability(
        name=name,
        available=True,
        source="models.router",
        detail=f"{model}{extra}".strip(),
    )


def _search_detail() -> str:
    try:
        from tools.web_search_tool import resolve_search_provider

        return f"provider={resolve_search_provider()}"
    except Exception as exc:
        return f"provider unresolved ({exc})"


def list_capabilities() -> list[Capability]:
    tools_ok = _has_tool("read_file", "list_directory")
    caps = [
        _model_cap("chat", CAP_CHAT, "brain"),
        _model_cap("code", CAP_CODE, "code"),
        _model_cap("vision", CAP_VISION, "vision"),
        _model_cap("asr", CAP_ASR, "asr"),
        _model_cap("tts", CAP_TTS, "tts"),
        _model_cap("embed", CAP_EMBED, "embed"),
        _model_cap("rerank", CAP_RERANK, "rerank"),
        Capability(
            "local_files",
            tools_ok,
            source="ToolRegistry",
            detail="read_file/list_directory" if tools_ok else "file tools not registered",
        ),
        Capability(
            "terminal",
            _has_tool("run_command"),
            source="ToolRegistry",
            requires_confirmation=True,
            detail="run_command",
        ),
        Capability(
            "browser",
            _has_tool("open_url", "browser_goal"),
            network=True,
            source="ToolRegistry",
            detail="open_url / Playwright",
        ),
        Capability(
            "web_search",
            _has_tool("web_search", "search_google"),
            network=True,
            source="ToolRegistry",
            detail=_search_detail() if _has_tool("web_search", "search_google") else "not registered",
        ),
        Capability(
            "inspect_url",
            _has_tool("inspect_url"),
            network=True,
            source="ToolRegistry",
            detail="fetch page text",
        ),
        Capability(
            "rag",
            True,
            source="knowledge.engine",
            detail="TurboVec + BGE-small",
        ),
        Capability(
            "project_index",
            True,
            source="knowledge.engine",
            detail="/open and HUD ingest",
        ),
        Capability(
            "git",
            _has_tool("git_status") and _which("git"),
            source="ToolRegistry",
            detail="git_* primitives via CommandTool" if _has_tool("git_status") else "not registered",
        ),
        Capability(
            "documents",
            _has_tool("extract_document") and _document_parsers_ok(),
            source="ToolRegistry",
            detail=_document_detail(),
        ),
        Capability(
            "docker",
            _has_tool("docker_ps") and _which("docker"),
            source="ToolRegistry",
            detail="docker_* inspect primitives via CommandTool" if _has_tool("docker_ps") else "not registered",
        ),
        Capability(
            "database",
            _has_tool("db_query", "db_list_connections"),
            source="ToolRegistry",
            detail=_database_detail(),
        ),
    ]
    for name, detail in UNSUPPORTED.items():
        caps.append(
            Capability(
                name=name,
                available=False,
                source="unsupported",
                detail=detail,
            )
        )
    return caps


def get_capability(name: str) -> Capability | None:
    for cap in list_capabilities():
        if cap.name == name:
            return cap
    return None


def capability_card() -> str:
    """Compact, prompt-safe. No model IDs or permission tables."""
    by_name = {c.name: c for c in list_capabilities()}
    lines = []
    for key, label in _CARD_LABELS:
        cap = by_name.get(key)
        if cap is None:
            continue
        state = "available" if cap.available else "unavailable"
        lines.append(f"{label}: {state}")
    return "\n".join(lines)


def capability_report() -> str:
    lines = ["Immortility capability report", ""]
    try:
        brain = model_id_for_role("brain")
        lines.append(f"Brain model: {brain or '(unset)'}")
        lines.append("")
    except Exception:
        pass
    try:
        from core.permissions import get_mode, mode_description

        mode = get_mode()
        lines.append(f"Permission mode: {mode} — {mode_description(mode)}")
        lines.append("")
    except Exception:
        pass
    for cap in list_capabilities():
        flag = "yes" if cap.available else "no"
        extra = f" — {cap.detail}" if cap.detail else ""
        net = " network" if cap.network else ""
        conf = " confirm" if cap.requires_confirmation else ""
        lines.append(f"{cap.name}: {flag} [{cap.source}{net}{conf}]{extra}")
    lines.append("")
    lines.append("Unsupported capabilities cannot be enabled by confirmation or env.")
    return "\n".join(lines)


def wants_capability_report(message: str) -> bool:
    low = (message or "").lower().strip()
    if not low:
        return False
    needles = (
        "tell me about yourself",
        "what can you do",
        "what you can do",
        "what are you able",
        "your capabilities",
        "what are you capable",
        "can you see images",
        "can you see pictures",
        "do you have internet",
        "do you have network",
        "what model are you",
        "what model are you running",
        "can you run commands",
        "can you search the web",
        "crack a wifi",
        "wifi password",
        "packet injection",
        "deauth",
    )
    return any(n in low for n in needles)


def answer_capability_question(message: str) -> str:
    """Deterministic answers for honesty regression prompts."""
    low = (message or "").lower()
    report = capability_report()
    card = capability_card()
    if "see image" in low or "see picture" in low or "see images" in low:
        cap = get_capability("vision")
        return f"VISION: {'available' if cap and cap.available else 'unavailable'}\n{cap.detail if cap else ''}".strip()
    if "what model" in low:
        brain = model_id_for_role("brain") or "(unset)"
        return f"I am Immortility. The brain model is {brain}."
    if "internet" in low or "network" in low:
        web = get_capability("web_search")
        inspect = get_capability("inspect_url")
        return (
            f"WEB SEARCH: {'available' if web and web.available else 'unavailable'}"
            f" ({web.detail if web else ''})\n"
            f"URL INSPECTION: {'available' if inspect and inspect.available else 'unavailable'}"
        )
    if "wifi" in low or "packet" in low or "deauth" in low or "credential" in low:
        return "Intentionally not supported."
    if "run command" in low or "terminal" in low:
        cap = get_capability("terminal")
        return f"TERMINAL: {'available' if cap and cap.available else 'unavailable'}"
    if "search the web" in low or "web search" in low:
        cap = get_capability("web_search")
        return (
            f"WEB SEARCH: {'available' if cap and cap.available else 'unavailable'}\n"
            f"{cap.detail if cap else ''}"
        ).strip()
    return f"{card}\n\n{report}"


def permissions_for_tool(name: str) -> dict[str, bool]:
    """Derive read/write/network/destructive from existing confirmation sets."""
    from core.pending_action import (
        CONFIRMATION_TOOLS,
        DIRECT_BROWSER_TOOLS,
        READ_ONLY_TOOLS,
    )

    read = name in READ_ONLY_TOOLS or name in {
        "read_file",
        "list_directory",
        "inspect_url",
        "web_search",
        "search_google",
        "get_system_status",
    }
    write = name in CONFIRMATION_TOOLS and name not in READ_ONLY_TOOLS
    network = name in DIRECT_BROWSER_TOOLS or name in {
        "inspect_url",
        "web_search",
        "search_google",
        "scrape_page",
        "open_url",
        "browser_goal",
    }
    destructive = name in {
        "delete_file",
        "kill_process",
        "run_command",
        "git_reset",
        "git_push",
        "git_branch",
        "docker_rm",
        "db_execute",
        "write_file",
        "apply_patch",
    }
    if name in READ_ONLY_TOOLS:
        write = False
        destructive = False
    if name in DIRECT_BROWSER_TOOLS:
        read = True
        write = False
    return {
        "read": bool(read or (not write and not destructive)),
        "write": bool(write),
        "network": bool(network),
        "destructive": bool(destructive),
    }
