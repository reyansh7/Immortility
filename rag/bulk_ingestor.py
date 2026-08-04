"""Multi-project bulk ingestion pipeline.

Discovers sub-projects under a root directory and indexes them into the
shared TurboVec store. Prints line-by-line terminal progress so HUD/CLI
users can see every project and file milestone.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rag.embeddings import EmbeddingModel
from rag.indexer import Indexer
from rag.project_indexer import (
    ProjectIndexer,
    ProjectInfo,
    SKIP_DIRS,
)
from rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def _default_progress(msg: str) -> None:
    try:
        from rich.console import Console

        Console().print(msg, soft_wrap=True)
    except Exception:
        print(msg, flush=True)
    else:
        import sys

        sys.stdout.flush()
        sys.stderr.flush()


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
    skipped: bool = False


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
            f"  Projects indexed: {self.total_projects}",
            f"  Skipped (already had chunks): {len(self.skipped_projects)}",
            f"  Files: {self.total_files}",
            f"  Chunks: {self.total_chunks}",
            f"  Time: {self.total_time_seconds:.1f}s",
        ]
        if self.skipped_projects:
            lines.append(
                f"  Skipped names: {', '.join(self.skipped_projects)}"
            )
        return "\n".join(lines)


class BulkIngestor:
    """Discovers and indexes multiple projects under a root directory."""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedding_model: EmbeddingModel | None = None,
        extra_skip_dirs: set[str] | None = None,
        progress: ProgressFn | None = None,
    ) -> None:
        self._store = vector_store or VectorStore()
        self._embedder = embedding_model or EmbeddingModel()
        self._indexer = Indexer(
            vector_store=self._store,
            embedding_model=self._embedder,
        )
        self._project_indexer = ProjectIndexer(self._indexer)
        self._skip_dirs = set(SKIP_DIRS) | (extra_skip_dirs or set())
        self._progress = progress or _default_progress

    def _log(self, msg: str) -> None:
        # Never let console encoding failures abort indexing (Windows cp1252).
        try:
            self._progress(msg)
        except UnicodeEncodeError:
            try:
                self._progress(msg.encode("ascii", "replace").decode("ascii"))
            except Exception as exc:
                logger.debug("progress callback failed: %s", exc)
        except Exception as exc:
            logger.debug("progress callback failed: %s", exc)
        try:
            logger.info("%s", msg)
        except Exception:
            pass

    def discover_projects(self, root_dir: Path) -> list[Path]:
        """Find first-level subdirectories that look like projects."""
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

    def is_already_indexed(self, project_name: str) -> bool:
        """True if TurboVec already has chunks tagged with this project name."""
        try:
            return self._store.count_by_project(project_name) > 0
        except Exception:
            return False

    def ingest_project(
        self,
        project_path: Path,
        name: str | None = None,
        *,
        project_index: int | None = None,
        project_total: int | None = None,
    ) -> ProjectIngestResult:
        """Index a single project directory with visible progress."""
        project_name = name or project_path.name
        result = ProjectIngestResult(
            name=project_name,
            path=str(project_path),
        )
        prefix = ""
        if project_index is not None and project_total is not None:
            prefix = f"[{project_index}/{project_total}] "

        t0 = time.perf_counter()

        # If TurboVec has no chunks for this project but index_state still has
        # file hashes, clear them so we actually re-embed (orphan repair).
        try:
            if self._store.count_by_project(project_name) == 0:
                cleared = self._indexer.state.remove_under(project_path)
                if cleared:
                    self._log(
                        f"  {prefix}{project_name}: cleared {cleared} orphaned "
                        f"hash entries (0 vectors in TurboVec)"
                    )
        except Exception as exc:
            logger.debug("orphan hash clear failed: %s", exc)

        def on_file(done: int, total: int, path: Path, chunks_added: int) -> None:
            # Print every file so the terminal never looks "stuck"
            if done == 0:
                self._log(
                    f"  {prefix}{project_name}: scanning... found {total} indexable files"
                )
                return
            try:
                rel = str(path.relative_to(project_path))
            except Exception:
                rel = path.name
            elapsed = time.perf_counter() - t0
            self._log(
                f"  {prefix}{project_name}: [{done}/{total}] "
                f"+{chunks_added} chunks  {rel}  ({elapsed:.1f}s)"
            )

        try:
            self._log(f"{prefix}START  {project_name}  ->  {project_path}")
            info: ProjectInfo = self._project_indexer.index_project(
                project_path,
                project_name,
                on_progress=on_file,
            )
            result.total_files = info.total_files
            result.total_chunks = info.total_chunks
            result.language = info.language
            result.framework = info.framework
            result.time_seconds = round(time.perf_counter() - t0, 2)
            self._log(
                f"{prefix}DONE   {project_name}: "
                f"{info.total_files} files -> {info.total_chunks} chunks "
                f"in {result.time_seconds:.1f}s"
            )
        except Exception as exc:
            result.error = str(exc)
            result.time_seconds = round(time.perf_counter() - t0, 2)
            self._log(f"{prefix}FAIL   {project_name}: {exc}")
            logger.error("Failed to index '%s': %s", project_name, exc)

        return result

    def ingest_paths(
        self,
        project_dirs: list[Path],
        *,
        force: bool = False,
    ) -> BulkIngestReport:
        """Index an explicit list of project directories (HUD bulk path)."""
        report = BulkIngestReport(
            root_dir=str(project_dirs[0].parent) if project_dirs else ""
        )
        t0 = time.perf_counter()
        total = len(project_dirs)
        self._log(f"[bold cyan]Bulk index: {total} project(s)  force={force}[/bold cyan]")

        for i, project_path in enumerate(project_dirs, 1):
            project_name = project_path.name
            existing = self._store.count_by_project(project_name)
            if not force and existing > 0:
                report.skipped_projects.append(project_name)
                self._log(
                    f"[{i}/{total}] SKIP  {project_name} "
                    f"(already has {existing} chunks)"
                )
                report.projects.append(
                    ProjectIngestResult(
                        name=project_name,
                        path=str(project_path),
                        total_chunks=existing,
                        skipped=True,
                    )
                )
                continue

            result = self.ingest_project(
                project_path,
                project_index=i,
                project_total=total,
            )
            report.projects.append(result)

        report.total_projects = sum(1 for p in report.projects if not p.skipped and not p.error)
        report.total_files = sum(r.total_files for r in report.projects if not r.skipped)
        report.total_chunks = sum(r.total_chunks for r in report.projects if not r.skipped)
        report.total_time_seconds = round(time.perf_counter() - t0, 2)
        self._log(report.summary())
        return report

    def ingest_all(
        self,
        root_dir: str | Path,
        force: bool = False,
        project_filter: str | None = None,
    ) -> BulkIngestReport:
        """Discover and index all projects under a root directory."""
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Root directory not found: {root}")

        project_dirs = self.discover_projects(root)
        if project_filter:
            project_dirs = [
                d for d in project_dirs
                if d.name.lower() == project_filter.lower()
            ]
        self._log(f"Discovered {len(project_dirs)} project(s) under {root}")
        report = self.ingest_paths(project_dirs, force=force)
        report.root_dir = str(root)
        return report
