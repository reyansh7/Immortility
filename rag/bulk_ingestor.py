"""Multi-project bulk ingestion pipeline.

Discovers sub-projects under a root directory and indexes them all
into the shared ChromaDB vector store.  Entirely decoupled from the
file-patching execution loop — can be run standalone via
``scripts/ingest_projects.py``.

Key design decisions:
- Reuses existing ``ProjectIndexer`` per-project (AST-first chunking).
- SHA-256 delta updates via ``IndexStateDB`` (unchanged files are skipped).
- Secret/noise denylists skip ``.env``, keys, and high-noise dirs.
- Batched ChromaDB inserts (500 per batch) to prevent memory spikes.
- Files > 1 MB are skipped to protect VRAM.
- Safe ``utf-8`` reading with ``errors='ignore'``.
- Rich progress bars for live feedback.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag.embeddings import EmbeddingModel
from rag.indexer import Indexer
from rag.project_indexer import (
    ProjectIndexer,
    ProjectInfo,
    SKIP_DIRS,
)
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class ProjectIngestResult:
    """Stats for a single project ingestion."""

    name: str
    path: str
    total_files: int = 0
    total_chunks: int = 0
    skipped_files: int = 0
    time_seconds: float = 0.0
    language: str = ""
    framework: str = ""
    error: str = ""


@dataclass
class BulkIngestReport:
    """Aggregated report across all ingested projects."""

    root_dir: str
    projects: list[ProjectIngestResult] = field(default_factory=list)
    total_projects: int = 0
    total_files: int = 0
    total_chunks: int = 0
    total_time_seconds: float = 0.0
    skipped_projects: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary for logging / CLI output."""
        lines = [
            f"Bulk Ingestion Report: {self.root_dir}",
            f"  Projects: {self.total_projects}",
            f"  Files: {self.total_files}",
            f"  Chunks: {self.total_chunks}",
            f"  Time: {self.total_time_seconds:.1f}s",
        ]
        if self.skipped_projects:
            lines.append(
                f"  Skipped (already indexed): {', '.join(self.skipped_projects)}"
            )
        return "\n".join(lines)


class BulkIngestor:
    """Discovers and indexes multiple projects under a root directory.

    Parameters:
        vector_store: Shared ChromaDB vector store.
        embedding_model: BGE embedding model.
        extra_skip_dirs: Additional directory names to skip.
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedding_model: EmbeddingModel | None = None,
        extra_skip_dirs: set[str] | None = None,
    ) -> None:
        self._store = vector_store or VectorStore()
        self._embedder = embedding_model or EmbeddingModel()
        self._indexer = Indexer(
            vector_store=self._store,
            embedding_model=self._embedder,
        )
        self._project_indexer = ProjectIndexer(self._indexer)
        self._skip_dirs = set(SKIP_DIRS) | (extra_skip_dirs or set())

    # ── Discovery ───────────────────────────────────────────────────

    def discover_projects(self, root_dir: Path) -> list[Path]:
        """Find all first-level subdirectories that look like projects.

        A directory is considered a project if:
        - It is a direct child of ``root_dir``.
        - It is not in ``SKIP_DIRS``.
        - It is not a hidden directory (starts with ``.``).

        Args:
            root_dir: Parent directory containing projects.

        Returns:
            Sorted list of project directory paths.
        """
        projects: list[Path] = []
        try:
            for child in sorted(root_dir.iterdir()):
                if not child.is_dir():
                    continue
                if child.name in self._skip_dirs:
                    continue
                if child.name.startswith("."):
                    continue
                projects.append(child)
        except PermissionError:
            logger.error("Permission denied: %s", root_dir)
        return projects

    # ── Index check ─────────────────────────────────────────────────

    def is_already_indexed(self, project_name: str) -> bool:
        """Check if a project already has chunks in the vector store.

        Uses ChromaDB metadata filter to check for existing chunks
        tagged with this project name.

        Args:
            project_name: Name of the project.

        Returns:
            True if chunks exist for this project.
        """
        self._store._ensure_connected()
        try:
            result = self._store._collection.get(
                where={"project": project_name},
                include=[],
                limit=1,
            )
            return bool(result and result.get("ids"))
        except Exception:
            return False

    # ── Single project ──────────────────────────────────────────────

    def ingest_project(
        self, project_path: Path, name: str | None = None
    ) -> ProjectIngestResult:
        """Index a single project directory.

        Args:
            project_path: Root of the project.
            name: Display name (defaults to directory name).

        Returns:
            ``ProjectIngestResult`` with stats.
        """
        project_name = name or project_path.name
        result = ProjectIngestResult(
            name=project_name,
            path=str(project_path),
        )

        t0 = time.perf_counter()
        try:
            info: ProjectInfo = self._project_indexer.index_project(
                project_path, project_name
            )
            result.total_files = info.total_files
            result.total_chunks = info.total_chunks
            result.language = info.language
            result.framework = info.framework
            result.time_seconds = round(time.perf_counter() - t0, 2)

            logger.info(
                "Indexed project '%s': %d files, %d chunks in %.1fs",
                project_name, info.total_files, info.total_chunks,
                result.time_seconds,
            )
        except Exception as exc:
            result.error = str(exc)
            result.time_seconds = round(time.perf_counter() - t0, 2)
            logger.error("Failed to index '%s': %s", project_name, exc)

        return result

    # ── Bulk ingestion ──────────────────────────────────────────────

    def ingest_all(
        self,
        root_dir: str | Path,
        force: bool = False,
        project_filter: str | None = None,
    ) -> BulkIngestReport:
        """Discover and index all projects under a root directory.

        Args:
            root_dir: Parent directory containing project subdirectories.
            force: If True, re-index even if already indexed.
            project_filter: If set, only index the project with this name.

        Returns:
            ``BulkIngestReport`` with aggregated stats.
        """
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Root directory not found: {root}")

        report = BulkIngestReport(root_dir=str(root))
        t0 = time.perf_counter()

        # Discover projects
        project_dirs = self.discover_projects(root)
        if project_filter:
            project_dirs = [
                d for d in project_dirs
                if d.name.lower() == project_filter.lower()
            ]

        logger.info(
            "Discovered %d project(s) under %s", len(project_dirs), root
        )

        # Try to use rich progress bar
        try:
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
            use_rich = True
        except ImportError:
            use_rich = False

        if use_rich:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
            ) as progress:
                task = progress.add_task(
                    "Ingesting projects...", total=len(project_dirs)
                )
                for project_path in project_dirs:
                    project_name = project_path.name
                    progress.update(
                        task, description=f"Indexing: {project_name}"
                    )

                    # Skip if already indexed (unless forced)
                    if not force and self.is_already_indexed(project_name):
                        report.skipped_projects.append(project_name)
                        progress.advance(task)
                        continue

                    result = self.ingest_project(project_path)
                    report.projects.append(result)
                    progress.advance(task)
        else:
            for i, project_path in enumerate(project_dirs, 1):
                project_name = project_path.name
                logger.info(
                    "[%d/%d] Indexing: %s",
                    i, len(project_dirs), project_name,
                )

                if not force and self.is_already_indexed(project_name):
                    report.skipped_projects.append(project_name)
                    continue

                result = self.ingest_project(project_path)
                report.projects.append(result)

        # Aggregate stats
        report.total_projects = len(report.projects)
        report.total_files = sum(r.total_files for r in report.projects)
        report.total_chunks = sum(r.total_chunks for r in report.projects)
        report.total_time_seconds = round(time.perf_counter() - t0, 2)

        logger.info(report.summary())
        return report
