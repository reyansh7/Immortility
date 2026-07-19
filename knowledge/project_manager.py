"""Project manager with background file watching and debounced re-indexing.

Handles:
- Opening and indexing projects
- Watching for file changes (watchdog)
- Debounced incremental re-indexing (3-second delay)
- Background worker queue so indexing never blocks the assistant
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from rag.indexer import Indexer
from rag.project_indexer import ProjectIndexer, ProjectInfo, SKIP_DIRS, SUPPORTED_EXTENSIONS, SUPPORTED_NAMES
from rag.security_filters import is_denied_dirname, should_index_path
from memory.project_memory import ProjectMemory

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3.0


# ── File watcher (watchdog) ─────────────────────────────────────────


class _FileEventHandler:
    """Watchdog event handler that enqueues file changes for debounced processing."""

    def __init__(self, job_queue: queue.Queue[str], project_name: str) -> None:
        self._queue = job_queue
        self._project = project_name
        self._last_events: dict[str, float] = {}

    def dispatch(self, event: Any) -> None:
        """Called by watchdog for every file-system event."""
        if getattr(event, "is_directory", False):
            return

        src_path: str = getattr(event, "src_path", "")
        if not src_path:
            return

        path = Path(src_path)

        # Filter by supported extensions
        if path.name not in SUPPORTED_NAMES and path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        # Filter by skip / denied directories and secret files
        parts = path.parts
        if any(skip in parts for skip in SKIP_DIRS) or any(is_denied_dirname(p) for p in parts):
            return
        if not should_index_path(path):
            return

        # Debounce: only enqueue if >DEBOUNCE_SECONDS since last event for this file
        now = time.time()
        last = self._last_events.get(src_path, 0.0)
        if now - last < DEBOUNCE_SECONDS:
            return

        self._last_events[src_path] = now
        event_type = getattr(event, "event_type", "modified")
        self._queue.put(f"{event_type}:{src_path}")
        logger.debug("Enqueued %s: %s", event_type, src_path)


class _BackgroundWorker(threading.Thread):
    """Background thread that processes the indexing job queue."""

    def __init__(
        self,
        job_queue: queue.Queue[str],
        indexer: Indexer,
        project_name: str,
        retriever_invalidate: Any | None = None,
        cache_invalidate: Any | None = None,
        structure_reindex: Any | None = None,
    ) -> None:
        super().__init__(daemon=True, name="IndexWorker")
        self._queue = job_queue
        self._indexer = indexer
        self._project = project_name
        self._retriever_invalidate = retriever_invalidate
        self._cache_invalidate = cache_invalidate
        self._structure_reindex = structure_reindex
        self._running = True

    def run(self) -> None:
        """Process jobs from the queue until stopped."""
        logger.info("Background indexing worker started.")
        while self._running:
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if job == "STOP":
                break

            try:
                event_type, filepath = job.split(":", 1)

                # Wait for debounce period
                time.sleep(DEBOUNCE_SECONDS)

                if event_type in ("modified", "created"):
                    if Path(filepath).exists():
                        chunks = self._indexer.reindex_file(filepath, self._project)
                        logger.info(
                            "Re-indexed %s: %d chunks", filepath, chunks
                        )
                        if self._structure_reindex:
                            try:
                                self._structure_reindex(filepath, self._project)
                            except Exception as exc:
                                logger.debug("structure_reindex failed: %s", exc)
                elif event_type == "deleted":
                    removed = self._indexer.remove_file(filepath)
                    logger.info("Removed index for %s: %d chunks", filepath, removed)

                # Invalidate caches
                if self._retriever_invalidate:
                    self._retriever_invalidate()
                if self._cache_invalidate:
                    self._cache_invalidate(filepath)

            except Exception as exc:
                logger.error("Background indexing error: %s", exc)
            finally:
                self._queue.task_done()

        logger.info("Background indexing worker stopped.")

    def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False
        self._queue.put("STOP")


# ── Project manager ─────────────────────────────────────────────────


class ProjectManager:
    """Manages project lifecycle: open, index, watch, remember.

    Parameters:
        indexer: File-level indexer.
        project_indexer: Project-level indexer.
        project_memory: Persistent project memory.
    """

    def __init__(
        self,
        indexer: Indexer | None = None,
        project_indexer: ProjectIndexer | None = None,
        project_memory: ProjectMemory | None = None,
        graph_engine: Any | None = None,
    ) -> None:
        self._indexer = indexer or Indexer()
        self._project_indexer = project_indexer or ProjectIndexer(self._indexer)
        self._project_memory = project_memory or ProjectMemory()
        self._graph_engine = graph_engine
        self._active_project: ProjectInfo | None = None
        self._observer: Any = None
        self._worker: _BackgroundWorker | None = None
        self._job_queue: queue.Queue[str] = queue.Queue()
        self._retriever_invalidate = None
        self._cache_invalidate = None
        self._structure_reindex = None

    # ── Dependency injection for invalidation callbacks ─────────────

    def set_invalidation_callbacks(
        self,
        retriever_invalidate: Any = None,
        cache_invalidate: Any = None,
        structure_reindex: Any = None,
    ) -> None:
        """Set callbacks for cache/retriever/structure invalidation on file changes."""
        self._retriever_invalidate = retriever_invalidate
        self._cache_invalidate = cache_invalidate
        self._structure_reindex = structure_reindex

    # ── Open / index ────────────────────────────────────────────────

    def open_project(
        self, path: str | Path, name: str | None = None
    ) -> ProjectInfo:
        """Open and index a project directory.

        Args:
            path: Root path of the project.
            name: Optional display name (defaults to directory name).

        Returns:
            ``ProjectInfo`` with detected metadata and indexing stats.
        """
        root = Path(path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Project path not found: {root}")

        project_name = name or root.name

        # Stop any existing watcher
        self.stop_watching()

        # Index the project
        info = self._project_indexer.index_project(root, project_name)
        self._active_project = info

        # Generate structural graph
        if self._graph_engine:
            # Optionally could be run in background, but keeping it sync here
            self._graph_engine.generate_and_load(root)

        # Remember in project memory
        self._project_memory.remember_project(info.to_dict())

        # Start watching for changes
        self.watch_project(root, project_name)

        logger.info(
            "Project opened: %s (%d files, %d chunks, %.1fs)",
            project_name,
            info.total_files,
            info.total_chunks,
            info.index_time_seconds,
        )
        return info

    def get_active_project(self) -> ProjectInfo | None:
        """Return the currently active project, or ``None``."""
        return self._active_project

    def get_active_project_name(self) -> str:
        """Return the active project name, or empty string."""
        return self._active_project.name if self._active_project else ""

    # ── File watching ───────────────────────────────────────────────

    def watch_project(self, path: str | Path, project_name: str) -> None:
        """Start background file watching with debounced re-indexing.

        Args:
            path: Project root to watch.
            project_name: Project name for metadata tagging.
        """
        try:
            # pyrefly: ignore [missing-import]
            from watchdog.observers import Observer
            # pyrefly: ignore [missing-import]
            from watchdog.events import FileSystemEventHandler

            # Create a proper watchdog handler that delegates to our handler
            handler_delegate = _FileEventHandler(self._job_queue, project_name)

            class WatchdogHandler(FileSystemEventHandler):
                def on_any_event(self, event: Any) -> None:
                    handler_delegate.dispatch(event)

            self._observer = Observer()
            self._observer.schedule(
                WatchdogHandler(),
                str(Path(path).resolve()),
                recursive=True,
            )
            self._observer.daemon = True
            self._observer.start()

            # Start the background worker
            self._worker = _BackgroundWorker(
                self._job_queue,
                self._indexer,
                project_name,
                self._retriever_invalidate,
                self._cache_invalidate,
                self._structure_reindex,
            )
            self._worker.start()

            logger.info("File watcher started for: %s", path)
        except ImportError:
            logger.warning("watchdog not installed — file watching disabled.")
        except Exception as exc:
            logger.error("Failed to start file watcher: %s", exc)

    def stop_watching(self) -> None:
        """Stop the file watcher and background worker."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3.0)
            except Exception as exc:
                logger.warning("Error stopping observer: %s", exc)
            self._observer = None

        if self._worker is not None:
            self._worker.stop()
            self._worker.join(timeout=3.0)
            self._worker = None

        logger.debug("File watcher stopped.")

    # ── Restore active project ──────────────────────────────────────

    def restore_project(self, project_name: str) -> ProjectInfo | None:
        """Restore a previously remembered project without full re-indexing.

        Args:
            project_name: Name of the project to restore.

        Returns:
            ``ProjectInfo`` if found in memory, else ``None``.
        """
        data = self._project_memory.get_project(project_name)
        if data is None:
            return None

        info = ProjectInfo.from_dict(data)
        self._active_project = info

        # Start watcher if the path still exists
        path = Path(info.path)
        if path.is_dir():
            self.watch_project(path, project_name)

        logger.info("Restored project from memory: %s", project_name)
        return info

    # ── Cleanup ─────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Clean shutdown: stop watcher, drain queue."""
        self.stop_watching()
        logger.info("ProjectManager shut down.")
