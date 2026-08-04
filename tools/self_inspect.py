"""Local Immortility self-inspection: RAG, knowledge/, vector DB, own codebase.

HUD/chat must use this instead of web search or "I cannot access files".
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent

# Self / local-code intents (not generic "what is a vector database?")
_SELF_PATTERNS = (
    r"\byour\s+(vector|rag|knowledge|codebase|code\s*base|files?|folder)\b",
    r"\b(immortility|immortality)\s+(codebase|code|rag|vector|knowledge)\b",
    r"\b(read|list|open|analyze|analyse|inspect|scan|show|summarize|summarise)\b.*"
    r"\b(knowledge|rag|vector\s*db|vector\s*database|chromadb|\.vector_db)\b",
    r"\b(knowledge|rag)\s+(folder|directory|code|files?|module)\b",
    r"\bwhere\b.*\b(rag|vector)\b",
    r"\b(show|open)\b.*\brag\b.*\bfile\b",
    r"\bwhere\s+did\s+i\s+use\s+rag\b",
    r"\blist\b.*\b(folder|directory)\b.*\b(codebase|immortility)\b",
    r"\bread\b.*\bknowledge\b",
    r"\banalyze\b.*\byour\b.*\bvector\b",
)

_FOLDER_ALIASES = {
    "knowledge": "knowledge",
    "rag": "rag",
    "memory": "memory",
    "core": "core",
    "tools": "tools",
    "frontend": "frontend",
}


def project_root() -> Path:
    return _ROOT


def wants_self_inspect(message: str) -> bool:
    low = (message or "").lower().strip()
    if not low:
        return False
    return any(re.search(p, low) for p in _SELF_PATTERNS)


def _read_py_files(folder: Path, *, max_files: int = 24, max_chars: int = 3500) -> list[tuple[str, str]]:
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.py"))
    out: list[tuple[str, str]] = []
    for fp in files[:max_files]:
        if fp.name == "__init__.py" and fp.stat().st_size < 80:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            out.append((fp.name, f"(unreadable: {exc})"))
            continue
        out.append((fp.name, text[:max_chars]))
    return out


def _folder_inventory(rel: str) -> str:
    folder = _ROOT / rel
    if not folder.is_dir():
        return f"Folder `{rel}` does not exist under {_ROOT}."
    lines = [f"**Path:** `{folder}`", "", "| File | Role (from docstring/header) |", "|---|---|"]
    for name, text in _read_py_files(folder, max_files=40, max_chars=800):
        role = _first_doc_line(text) or "(no docstring)"
        lines.append(f"| `{rel}/{name}` | {role} |")
    return "\n".join(lines)


def _first_doc_line(text: str) -> str:
    m = re.search(r'"""(.*?)"""', text, flags=re.S)
    if not m:
        m = re.search(r"'''(.*?)'''", text, flags=re.S)
    if not m:
        return ""
    for line in m.group(1).strip().splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return ""


def _vector_db_report() -> str:
    lines = [
        f"**Immortility root:** `{_ROOT}`",
        f"**TurboVec persist dir:** `{_ROOT / '.vector_db'}`",
        "",
    ]
    try:
        from knowledge.engine import KnowledgeEngine

        stats = KnowledgeEngine().stats()
        vs = stats.get("vector_store") or {}
        docs = stats.get("docs_store") or {}
        lines.extend(
            [
                "### Vector store (code_chunks)",
                f"- Collection: `{vs.get('collection')}`",
                f"- Persist: `{vs.get('persist_dir')}`",
                f"- Chunks indexed: **{vs.get('total_chunks', 0)}**",
                "",
                "### Documentation store",
                f"- Collection: `{docs.get('collection')}`",
                f"- Chunks: **{docs.get('total_chunks', 0)}**",
                "",
                f"- Active project: `{stats.get('active_project')}`",
                f"- Remembered projects: `{', '.join(stats.get('remembered_projects') or []) or '(none)'}`",
            ]
        )
    except Exception as exc:
        lines.append(f"KnowledgeEngine.stats() failed: {exc}")

    # Sample chunk metadata (no giant payloads)
    try:
        from rag.vector_store import VectorStore

        store = VectorStore(persist_dir=str(_ROOT / ".vector_db"))
        all_docs = store.get_all_documents()
        if not all_docs:
            lines.extend(
                [
                    "",
                    "### Sample chunks",
                    "Store is **empty**. Index a project (or Immortility itself) so RAG can retrieve code.",
                    f"RAG implementation lives in `{_ROOT / 'rag'}` — key files: "
                    "`vector_store.py`, `retriever.py`, `indexer.py`, `hybrid_search.py`, `embeddings.py`.",
                ]
            )
        else:
            lines.extend(["", "### Sample indexed chunks (up to 12)"])
            for row in all_docs[:12]:
                md = row.get("metadata") or {}
                fn = md.get("filename") or md.get("path") or row.get("id")
                snippet = (row.get("document") or "").strip().replace("\n", " ")[:140]
                lines.append(f"- `{fn}` — {snippet}")
    except Exception as exc:
        lines.append(f"\nCould not sample vector DB: {exc}")

    lines.extend(
        [
            "",
            "### Where RAG is implemented (source)",
            _folder_inventory("rag"),
        ]
    )
    return "\n".join(lines)


def _summarize_with_llm(title: str, evidence: str, user_ask: str) -> str:
    from core.llm import fast_chat

    system = (
        "You are Immortility inspecting YOUR OWN local codebase on disk. "
        f"Root: {_ROOT}. "
        "Use ONLY the evidence below. Never invent web links. "
        "Never say you cannot access files — the evidence was read from disk. "
        "Be concrete: name real files, classes, and what each does. "
        "Address Reyansh briefly."
    )
    prompt = (
        f"User asked: {user_ask}\n\n"
        f"## {title}\n\n"
        f"{evidence[:14000]}\n\n"
        "Write a clear Markdown summary of what you analyzed."
    )
    try:
        return fast_chat(
            prompt,
            history=[],
            system=system,
            max_output_tokens=700,
        ).strip()
    except Exception as exc:
        logger.warning("self-inspect LLM summarize failed: %s", exc)
        return f"## {title}\n\n{evidence[:6000]}"


def _detect_target_folder(message: str) -> str | None:
    low = message.lower()
    for alias, rel in _FOLDER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            # Prefer explicit folder asks
            if any(
                w in low
                for w in (
                    "folder", "directory", "read", "list", "open",
                    "analyze", "analyse", "summar", "show", "inspect",
                )
            ) or alias in ("knowledge", "rag"):
                return rel
    return None


def _ensure_self_indexed_hint(stats_chunks: int) -> str:
    if stats_chunks > 0:
        return ""
    return (
        "\n\n_Note: your vector DB has 0 code chunks yet. "
        "Say **index immortility** (or open this project in CLI) to store "
        "the codebase in TurboVec for RAG retrieval._"
    )


def handle_self_inspect(message: str) -> str:
    """Answer self/RAG/knowledge/vector-DB questions from local disk."""
    low = (message or "").lower()
    root = project_root()

    # Vector DB / TurboVec analysis
    if re.search(r"\b(vector\s*db|vector\s*database|turbovec|chromadb|\.vector_db|your\s+vector)\b", low):
        report = _vector_db_report()
        chunks = 0
        try:
            from knowledge.engine import KnowledgeEngine

            chunks = int((KnowledgeEngine().stats().get("vector_store") or {}).get("total_chunks") or 0)
        except Exception:
            pass
        summary = _summarize_with_llm(
            "Local vector database + RAG source",
            report,
            message,
        )
        return summary + _ensure_self_indexed_hint(chunks)

    # Where is RAG / show RAG files
    if re.search(r"\brag\b", low) and re.search(
        r"\b(where|show|open|file|code|folder|use|used|implement)\b", low
    ):
        inv = _folder_inventory("rag")
        evidence = (
            f"RAG lives under `{root / 'rag'}`. "
            "There is no `rag_code.py` — the package is split across these modules:\n\n"
            f"{inv}\n\n"
            f"Orchestration entry: `{root / 'knowledge' / 'engine.py'}` (KnowledgeEngine)."
        )
        return _summarize_with_llm("RAG source map", evidence, message)

    # Read/list a codebase folder (knowledge, rag, …)
    folder = _detect_target_folder(message)
    if folder:
        files = _read_py_files(root / folder)
        if not files:
            return f"I looked under `{root / folder}` — no readable `.py` files found."
        blocks = [f"**Folder:** `{root / folder}` ({len(files)} files read)\n"]
        for name, text in files:
            blocks.append(f"### {folder}/{name}\n```python\n{text}\n```")
        evidence = "\n\n".join(blocks)
        return _summarize_with_llm(f"Folder analysis: {folder}/", evidence, message)

    # Generic "your codebase" / list one folder
    if re.search(r"\b(codebase|code\s*base)\b", low) or re.search(
        r"\blist\b.*\bfolder\b", low
    ):
        # List top-level dirs + point at knowledge as the RAG orchestration layer
        dirs = sorted(
            p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        evidence = (
            f"Immortility root: `{root}`\n"
            f"Top-level folders: {', '.join(dirs)}\n\n"
            "Primary self-knowledge surfaces:\n"
            f"- `{root / 'rag'}` — TurboVec store, indexer, retriever\n"
            f"- `{root / 'knowledge'}` — KnowledgeEngine (RAG + memory facade)\n"
            f"- `{root / 'memory'}` — preferences / conversation / projects\n"
            f"- `{root / '.vector_db'}` — on-disk TurboVec + sidecar\n"
        )
        return _summarize_with_llm("Immortility codebase map", evidence, message)

    # Fallback: still try knowledge + rag inventory
    evidence = (
        f"Root: `{root}`\n\n"
        + _folder_inventory("knowledge")
        + "\n\n"
        + _folder_inventory("rag")
    )
    return _summarize_with_llm("Immortility knowledge systems", evidence, message)


def try_index_self() -> dict[str, Any]:
    """Index Immortility itself into the vector DB for future RAG."""
    from knowledge.engine import KnowledgeEngine

    engine = KnowledgeEngine()
    info = engine.open_project(str(_ROOT), name="immortility1")
    return {
        "path": str(_ROOT),
        "name": info.name,
        "files": info.total_files,
        "chunks": info.total_chunks,
        "stats": engine.stats(),
    }
