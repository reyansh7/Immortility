"""Knowledge Engine — single interface for all RAG and memory operations.

The Router should interact ONLY with this module.  No other component
should directly access the Retriever, Memory Manager, or Context Builder.

Architecture:
    User → Router → Knowledge Engine
                       ├── Retriever (hybrid search)
                       ├── Memory Agent
                       ├── Context Ranker
                       ├── Context Builder
                       └── Project Manager
                     → Planner → Coder → Runner → Debugger
"""

from __future__ import annotations

import logging
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge.context_ranker import ContextRanker
from knowledge.graph_engine import GraphEngine
from knowledge.hierarchical_memory import HierarchicalMemory
from knowledge.knowledge_graph_db import KnowledgeGraphDB
from knowledge.project_manager import ProjectManager
from knowledge.retrieval_cache import RetrievalCache
from memory.memory_manager import MemoryManager
from rag.embeddings import EmbeddingModel
from rag.hybrid_search import HybridSearch
from rag.indexer import Indexer
from rag.project_indexer import ProjectIndexer, ProjectInfo
from rag.retriever import Retriever, RetrievalResult
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


class KnowledgeEngine:
    """Single interface between the Router and all RAG / Memory subsystems.

    Provides:
    - Project opening and indexing
    - Automatic context retrieval for user queries
    - Memory management (store / recall / forget)
    - Documentation import
    - Cache management

    IMPORTANT: This class is a process-wide singleton. Creating ``KnowledgeEngine()``
    anywhere must return the same instance so active-project state stays coherent.
    """

    _instance: "KnowledgeEngine | None" = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "KnowledgeEngine":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        with self._init_lock:
            if getattr(self, "_initialized", False):
                return
            # Do NOT set _initialized until the end — concurrent HUD threads can
            # otherwise get a half-built singleton (missing _project_manager).

            # Core components (lazy-loaded where possible)
            self._embedding_model = EmbeddingModel()
            self._vector_store = VectorStore()
            self._docs_store = VectorStore(collection_name="documentation")
            self._indexer = Indexer(
                vector_store=self._vector_store,
                embedding_model=self._embedding_model,
            )
            self._project_indexer = ProjectIndexer(self._indexer)
            self._hybrid_search = HybridSearch(
                vector_store=self._vector_store,
                embedding_model=self._embedding_model,
            )
            self._retriever = Retriever(
                vector_store=self._vector_store,
                embedding_model=self._embedding_model,
                hybrid_search=self._hybrid_search,
            )
            self._memory = MemoryManager()
            self._context_ranker = ContextRanker()
            self._retrieval_cache = RetrievalCache()
            self._graph_engine = GraphEngine()
            self._kg_db = KnowledgeGraphDB()
            self._hierarchical = HierarchicalMemory(db=self._kg_db)
            self._project_manager = ProjectManager(
                indexer=self._indexer,
                project_indexer=self._project_indexer,
                project_memory=self._memory.project,
                graph_engine=self._graph_engine,
            )

            # Wire up invalidation callbacks
            self._project_manager.set_invalidation_callbacks(
                retriever_invalidate=self._retriever.invalidate_cache,
                cache_invalidate=self._retrieval_cache.invalidate_file,
                structure_reindex=self._on_structure_reindex,
            )

            self._initialized = True
            logger.info("Knowledge Engine initialised.")

    def _on_structure_reindex(self, filepath: str, project: str) -> None:
        """Keep AST knowledge graph in sync when files change on disk."""
        try:
            self._hierarchical.ingest_file(filepath, project)
            active = self.get_active_project()
            if active and active.path:
                self._hierarchical.rebuild_folder_summaries(project, Path(active.path))
                self._hierarchical.rebuild_repo_summary(project, Path(active.path))
        except Exception as exc:
            logger.debug("Structure reindex skipped for %s: %s", filepath, exc)

    @classmethod
    def reset_instance(cls) -> None:
        """Test-only: drop the singleton so the next call constructs fresh."""
        cls._instance = None

    @property
    def graph_engine(self) -> GraphEngine:
        """Access the underlying graph engine."""
        return self._graph_engine

    # ── Project operations ──────────────────────────────────────────

    def open_project(
        self, path: str | Path, name: str | None = None
    ) -> ProjectInfo:
        """Open, index, and start watching a project.

        Args:
            path: Root path of the project directory.
            name: Optional display name.

        Returns:
            ``ProjectInfo`` with detected metadata and indexing stats.
        """
        t0 = time.perf_counter()
        info = self._project_manager.open_project(path, name)

        # Invalidate caches after indexing
        self._retriever.invalidate_cache()
        self._retrieval_cache.invalidate_all()

        # Phase 2: build AST knowledge graph + hierarchical summaries
        try:
            report = self._hierarchical.ingest_project(info.path, info.name)
            logger.info(
                "Knowledge graph: %d files, %d folders for '%s'",
                report.get("files_ingested", 0),
                report.get("folders", 0),
                info.name,
            )
        except Exception as exc:
            logger.warning("Knowledge graph ingest failed: %s", exc)

        elapsed = time.perf_counter() - t0
        logger.info(
            "Project '%s' ready: %d files, %d chunks in %.1fs",
            info.name, info.total_files, info.total_chunks, elapsed,
        )
        return info

    def lookup_symbol(self, name: str, project: str | None = None) -> list[dict[str, Any]]:
        """Query AST knowledge graph for a symbol (no vector DB / LLM)."""
        proj = project or self.get_active_project_name() or None
        return self._hierarchical.lookup_symbol(name, project=proj)

    def get_repo_summary(self, project: str | None = None) -> str:
        proj = project or self.get_active_project_name()
        if not proj:
            return ""
        return self._kg_db.get_repo_summary(proj)

    def list_project_facts(self, project: str | None = None) -> list[dict[str, str]]:
        proj = project or self.get_active_project_name()
        if not proj:
            return []
        return self._kg_db.list_facts(proj)

    def add_reflection(
        self,
        task: str,
        what_broke: str,
        what_fixed_it: str,
        files_modified: list[str],
        project: str | None = None,
    ) -> None:
        self._kg_db.add_reflection(
            task=task,
            what_broke=what_broke,
            what_fixed_it=what_fixed_it,
            files_modified=files_modified,
            project=project or self.get_active_project_name() or "",
        )

    def list_reflections(
        self, project: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Return recent structured reflections for a project (or all)."""
        proj = project if project is not None else (self.get_active_project_name() or "")
        return self._kg_db.list_reflections(project=proj or "", limit=limit)

    @staticmethod
    def format_reflections_block(reflections: list[dict], goal: str = "", limit: int = 5) -> str:
        from knowledge.reflection_format import format_reflections_block as _fmt

        return _fmt(reflections, goal=goal, limit=limit)

    def get_experience_context(self, query: str, limit: int = 3) -> str:
        """Relevant past bug/fix experiences for prompt injection."""
        try:
            from memory.experience_memory import ExperienceMemory, format_experiences_block

            entries = ExperienceMemory().get_relevant_experiences(query, limit=limit)
            return format_experiences_block(entries, limit=limit)
        except Exception as exc:
            logger.debug("experience context skipped: %s", exc)
            return ""

    def get_active_project(self) -> ProjectInfo | None:
        """Return the currently active project, or ``None``."""
        return self._project_manager.get_active_project()

    def get_active_project_name(self) -> str:
        """Return the active project name, or empty string."""
        return self._project_manager.get_active_project_name()

    def restore_project(self, name: str) -> ProjectInfo | None:
        """Restore a previously remembered project without full re-indexing."""
        return self._project_manager.restore_project(name)

    # ── Context retrieval ───────────────────────────────────────────

    @staticmethod
    def _log_event(
        mode: str,
        query: str,
        retrieved: str,
        verification_passed: bool | None = None,
        failure_reason: str = "",
    ) -> None:
        try:
            from core.repo_paths import logs_dir

            path = logs_dir() / "immortility_events.jsonl"
        except Exception:
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
            logger.debug("Could not write immortility_events log")

    def query_codebase_graph(self, target_symbol: str) -> str:
        """Graphify wrapper: symbol/file only input, plain text output."""
        symbol = (target_symbol or "").strip()
        if not symbol:
            return ""
        active = self._project_manager.get_active_project()
        project_path = Path(active.path).resolve() if active and active.path else None

        # Preferred path: graphify CLI query wrapper
        if project_path:
            try:
                cmd = ["graphify", "query", symbol]
                out = subprocess.run(
                    cmd,
                    cwd=str(project_path),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip()[:2500]
            except Exception:
                pass

        # Fallback: use loaded graph data through deterministic lookup
        if self._graph_engine.is_loaded():
            return self._graph_engine.query_relationships(symbol)[:2500]
        return ""

    def get_hybrid_results(
        self,
        query: str,
        n_results: int = 8,
        *,
        project: str | None = None,
    ) -> list[RetrievalResult]:
        """Hybrid BM25 + semantic retrieval, optional CrossEncoder rerank."""
        project_name = project
        if not project_name:
            try:
                from core.project_resolve import resolve_project_from_query

                project_name = resolve_project_from_query(query)
            except Exception:
                project_name = None
        if not project_name:
            project_name = self.get_active_project_name() or None
        # Pull a wider pool so rerank / ContextRanker have room to work
        pool = max(n_results * 3, 12)
        hits = self._hybrid_search.search(query, n_results=pool, project=project_name)

        # If scoped search is empty, fall back to global (wrong active project)
        if not hits and project_name:
            hits = self._hybrid_search.search(query, n_results=pool, project=None)

        results: list[RetrievalResult] = []
        for hit in hits:
            md = hit.metadata or {}
            results.append(
                RetrievalResult(
                    content=hit.content,
                    filename=hit.filename,
                    relevance_score=hit.score,
                    chunk_type=hit.chunk_type,
                    class_name=md.get("class_name", ""),
                    function_name=md.get("function_name", ""),
                    language=md.get("language", ""),
                    start_line=md.get("start_line", 0),
                    end_line=md.get("end_line", 0),
                    metadata=md,
                )
            )

        try:
            from rag.reranker import rerank_enabled, rerank_results

            if rerank_enabled() and results:
                results = rerank_results(query, results, top_k=n_results)
        except Exception as exc:
            logger.debug("Rerank skipped: %s", exc)

        return results[:n_results]

    def get_learned_context(self, query: str, n_results: int = 4) -> str:
        """Retrieve self-learned notes / web research / desktop inventory from docs store."""
        q = (query or "").strip()
        if not q:
            return ""
        try:
            emb = self._embedding_model.encode_query(q)
            hits = self._docs_store.search(emb, n_results=n_results)
        except Exception as exc:
            logger.debug("learned-context search failed: %s", exc)
            return ""
        lines: list[str] = []
        for h in hits or []:
            doc = ""
            meta: dict = {}
            if isinstance(h, dict):
                doc = str(h.get("document") or h.get("content") or "")
                meta = h.get("metadata") or {}
            else:
                doc = str(getattr(h, "document", "") or getattr(h, "content", "") or "")
                meta = getattr(h, "metadata", None) or {}
            snippet = doc.strip().replace("\n", " ")
            if not snippet:
                continue
            kind = meta.get("kind") or meta.get("source") or "learned"
            lines.append(f"[learned:{kind}] {snippet[:280]}")
        return "\n".join(lines[:n_results])

    def get_routing_context(self, query: str, n_results: int = 6) -> str:
        hits = self.get_hybrid_results(query, n_results=n_results)
        lines = []
        for h in hits:
            snippet = (h.content or "").strip().replace("\n", " ")
            lines.append(f"{h.filename}:{h.start_line}-{h.end_line} :: {snippet[:220]}")
        graph = self.query_codebase_graph(query)
        learned = self.get_learned_context(query, n_results=3)
        experiences = self.get_experience_context(query, limit=3)
        payload = "\n".join(lines[:n_results])
        if learned:
            payload = f"{payload}\n\nLearned memory:\n{learned}"
        if experiences:
            payload = f"{payload}\n\n{experiences}"
        if graph:
            payload = f"{payload}\n\nGraph:\n{graph}"
        self._log_event("routing_retrieval", query, payload)
        return payload[:4000]

    def get_action_context(self, query: str, n_results: int = 8) -> str:
        """Action-mode retrieval with one multi-hop graph expansion."""
        hits = self.get_hybrid_results(query, n_results=n_results)
        ranked = self._context_ranker.rank(hits, active_project=self.get_active_project_name())
        target_symbol = ""
        if ranked:
            target_symbol = ranked[0].function_name or ranked[0].class_name or ranked[0].filename
        one_hop = self.query_codebase_graph(target_symbol or query)

        multi_hop_query = target_symbol or query
        two_hop = self.query_codebase_graph(f"callers callees {multi_hop_query}")

        parts = []
        for h in ranked[:n_results]:
            parts.append(h.to_context_string()[:800])
        if one_hop:
            parts.append(f"Graph one-hop for {multi_hop_query}:\n{one_hop}")
        if two_hop:
            parts.append(f"Graph two-hop callers/callees for {multi_hop_query}:\n{two_hop}")
        payload = "\n\n".join(parts)
        learned = self.get_learned_context(query, n_results=3)
        experiences = self.get_experience_context(query, limit=3)
        if learned:
            payload = f"{payload}\n\nLearned memory:\n{learned}"
        if experiences:
            payload = f"{payload}\n\n{experiences}"
        self._log_event("action_retrieval", query, payload)
        return payload[:9000]

    def get_context(
        self,
        query: str,
        n_results: int = 12,
        current_file_state: str = "",
        recent_diff: str = "",
    ) -> str:
        """Retrieve structured context for a user query (Phase 3 Smart Builder).

        Layout (fixed order):
        Project Facts → Repo Summary → Graph Symbols → Semantic Chunks (≤4)
        → Current File/Diff → User Task
        """
        from core.text_sanitize import sanitize_text
        from knowledge.smart_context_builder import SmartContextBuilder

        query = sanitize_text(query)
        project_name = self.get_active_project_name()

        cache_key = f"{query}||{bool(current_file_state)}||{bool(recent_diff)}"
        cached = self._retrieval_cache.get(cache_key, project_name)
        if cached is not None:
            logger.debug("Cache hit for query: %s", query[:60])
            return cached

        t0 = time.perf_counter()

        # Semantic retrieval — builder will keep top 4 only
        results: list[RetrievalResult] = self.get_hybrid_results(
            query, n_results=max(n_results, 8)
        )
        results = self._context_ranker.rank(results, active_project=project_name)

        # Graph symbols from AST knowledge graph (not full dependent files)
        builder = SmartContextBuilder()
        graph_hits: list[dict[str, Any]] = []
        for sym in builder.extract_symbol_candidates(query):
            graph_hits.extend(self.lookup_symbol(sym, project=project_name or None))
        # Dedupe by symbol+path
        seen: set[str] = set()
        unique_hits: list[dict[str, Any]] = []
        for h in graph_hits:
            key = f"{h.get('symbol')}|{h.get('file_path')}"
            if key in seen:
                continue
            seen.add(key)
            unique_hits.append(h)

        facts = self.list_project_facts(project_name or None)
        # Seed architecture facts from active project profile when empty
        active = self._project_manager.get_active_project()
        if active and not any(f.get("key") == "framework" for f in facts):
            if active.framework:
                facts = list(facts) + [{"key": "framework", "value": active.framework}]
            if active.language:
                facts = list(facts) + [{"key": "language", "value": active.language}]

        repo_summary = self.get_repo_summary(project_name or None)
        if not repo_summary and active:
            repo_summary = active.summary()

        context = builder.build(
            user_task=query,
            project_facts=facts,
            repo_summary=repo_summary,
            graph_hits=unique_hits,
            semantic_chunks=results,
            current_file_state=current_file_state,
            recent_diff=recent_diff,
        )

        self._retrieval_cache.put(cache_key, project_name, context)

        elapsed = time.perf_counter() - t0
        logger.info(
            "Smart context for '%s': %d chars in %.3fs",
            query[:60], len(context), elapsed,
        )
        self._log_event("context_retrieval", query, context)
        return context

    # ── Memory operations ───────────────────────────────────────────

    def remember(self, category: str, key: str, value: Any) -> bool:
        """Store a memory entry."""
        return self._memory.store(category, key, value)

    def recall(self, category: str, query: str = "") -> Any:
        """Retrieve memory entries."""
        return self._memory.recall(category, query)

    def forget(self, category: str, key: str) -> bool:
        """Remove a memory entry."""
        return self._memory.forget(category, key)

    def remember_current_project(self) -> bool:
        """Store the active project in long-term project memory."""
        active = self._project_manager.get_active_project()
        if active is None:
            logger.warning("No active project to remember.")
            return False
        self._memory.project.remember_project(active.to_dict())
        logger.info("Remembered project: %s", active.name)
        return True

    def remember_file(self, path: str | Path, note: str = "") -> dict[str, Any]:
        """Read a full file line-by-line and store a durable analysis note.

        This is how Immortility "learns" specific files you point at.
        Indexed retrieval covers the whole project; this stores an explicit
        full-file digest you can later ask about by name.
        """
        file_path = Path(path).resolve()
        if not file_path.is_file():
            return {"status": "error", "message": f"Not a file: {file_path}"}

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"status": "error", "message": str(exc)}

        lines = content.splitlines()
        preview = "\n".join(lines[:80])
        digest = (
            f"File: {file_path}\n"
            f"Lines: {len(lines)}\n"
            f"Bytes: {len(content.encode('utf-8', errors='replace'))}\n"
            f"Note: {note or 'user-requested deep read'}\n"
            f"Head:\n{preview}"
        )
        key = f"file:{file_path.name}"
        self._memory.store("conversation", key, digest[:4000])
        # Also keep a short project-scoped pointer
        active = self.get_active_project_name() or "global"
        self._memory.store(
            "project",
            f"{active}:{file_path.name}",
            {
                "name": f"{active}:{file_path.name}",
                "path": str(file_path),
                "lines": len(lines),
                "notes": (note or "deep-read")[:500],
            },
        )
        self._log_event("file_remember", str(file_path), digest[:2000])
        # Phase 2: refresh structured AST summary for this file
        try:
            proj = self.get_active_project_name() or "global"
            self._hierarchical.ingest_file(file_path, proj)
            active = self.get_active_project()
            if active and active.path:
                self._hierarchical.rebuild_folder_summaries(proj, Path(active.path))
                self._hierarchical.rebuild_repo_summary(proj, Path(active.path))
        except Exception as exc:
            logger.debug("Structured summary update skipped: %s", exc)
        return {
            "status": "success",
            "path": str(file_path),
            "line_count": len(lines),
            "message": f"Remembered {file_path.name} ({len(lines)} lines)",
        }

    # ── Documentation import ────────────────────────────────────────

    def import_docs(self, name: str, path: str | Path) -> int:
        """Import documentation files into a separate TurboVec collection.

        Args:
            name: Name for the documentation set (e.g. "fastapi", "yolo").
            path: Path to a directory of documentation files.

        Returns:
            Number of chunks indexed.
        """
        from rag.chunker import Chunker

        doc_path = Path(path).resolve()
        if not doc_path.is_dir():
            logger.error("Documentation path not found: %s", doc_path)
            return 0

        chunker = Chunker()
        doc_indexer = Indexer(
            vector_store=self._docs_store,
            embedding_model=self._embedding_model,
            chunker=chunker,
        )

        total = 0
        supported = {".md", ".txt", ".html", ".rst", ".py", ".json"}
        for fp in doc_path.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in supported:
                try:
                    total += doc_indexer.index_file(fp, project=name)
                except Exception as exc:
                    logger.error("Failed to index doc %s: %s", fp, exc)

        logger.info("Imported %d documentation chunks for '%s'", total, name)
        return total

    # ── Conversation extraction ─────────────────────────────────────

    def extract_memories(self, messages: list[dict[str, str]]) -> None:
        """Auto-extract memories from conversation history."""
        self._memory.extract_from_conversation(messages)

    # ── Bulk ingestion ──────────────────────────────────────────────

    def ingest_directory(
        self,
        root_dir: str | Path,
        force: bool = False,
        project_filter: str | None = None,
    ) -> dict[str, Any]:
        """Bulk-ingest all projects under a root directory.

        This is the programmatic entry point for bulk ingestion,
        callable from the main loop (e.g. via a ``/ingest`` command)
        without going through the code-patching workflow.

        Args:
            root_dir: Parent directory containing project subdirectories.
            force: If True, re-index even if already indexed.
            project_filter: If set, only index the named project.

        Returns:
            Summary dict with total projects, files, chunks, and time.
        """
        from rag.bulk_ingestor import BulkIngestor

        ingestor = BulkIngestor(
            vector_store=self._vector_store,
            embedding_model=self._embedding_model,
        )
        report = ingestor.ingest_all(
            root_dir=root_dir,
            force=force,
            project_filter=project_filter,
        )

        # Invalidate caches after bulk ingestion
        self._retriever.invalidate_cache()
        self._retrieval_cache.invalidate_all()

        logger.info(
            "Bulk ingestion complete: %d projects, %d files, %d chunks in %.1fs",
            report.total_projects,
            report.total_files,
            report.total_chunks,
            report.total_time_seconds,
        )

        return {
            "total_projects": report.total_projects,
            "total_files": report.total_files,
            "total_chunks": report.total_chunks,
            "total_time_seconds": report.total_time_seconds,
            "skipped_projects": report.skipped_projects,
            "projects": [
                {
                    "name": r.name,
                    "files": r.total_files,
                    "chunks": r.total_chunks,
                    "time": r.time_seconds,
                    "language": r.language,
                    "framework": r.framework,
                    "error": r.error,
                    "skipped": r.skipped,
                }
                for r in report.projects
            ],
        }

    # ── Self-learning ─────────────────────────────────────────────────

    def learn_note(
        self,
        text: str,
        *,
        source: str = "manual",
        kind: str = "note",
    ) -> dict[str, Any]:
        """Store a free-form note into the documentation TurboVec collection."""
        from knowledge.learner import remember_text

        return remember_text(text, source=source, kind=kind)

    # ── Stats ───────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return statistics about the Knowledge Engine."""
        return {
            "vector_store": self._vector_store.stats(),
            "docs_store": self._docs_store.stats(),
            "cache": self._retrieval_cache.stats(),
            "active_project": self.get_active_project_name() or None,
            "remembered_projects": self._memory.project.list_projects(),
            "conversation_entries": self._memory.conversation.count(),
            "preferences": self._memory.preferences.count(),
        }

    # ── Lifecycle ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Clean shutdown: stop watchers, clear session memory."""
        self._project_manager.shutdown()
        self._memory.clear_session()
        logger.info("Knowledge Engine shut down.")
