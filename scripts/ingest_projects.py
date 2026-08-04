"""Standalone CLI for bulk project ingestion.

Usage:
    python -m scripts.ingest_projects "C:\\Users\\reyan\\OneDrive\\Desktop\\Projects"
    python -m scripts.ingest_projects "C:\\path" --force          # re-index all
    python -m scripts.ingest_projects "C:\\path" --project MyApp  # index one project
    python -m scripts.ingest_projects "C:\\path" --stats          # show DB stats only

This script is entirely decoupled from the main assistant loop.
It does NOT import main.py, the action engine, or the patch generator.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure the project root is on sys.path so imports resolve
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-ingest project directories into the local RAG vector store.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.ingest_projects "C:\\Users\\reyan\\OneDrive\\Desktop\\Projects"
  python -m scripts.ingest_projects "./projects" --force
  python -m scripts.ingest_projects "./projects" --project MyApp
  python -m scripts.ingest_projects --stats
        """,
    )
    parser.add_argument(
        "root_dir",
        nargs="?",
        default=None,
        help="Root directory containing project subdirectories.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index all projects even if already indexed.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Index only the named project (subdirectory name).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show vector store statistics and exit.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    # ── Logging ─────────────────────────────────────────────────────
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler("immortility.log", encoding="utf-8"),
        ],
    )
    # Suppress noisy third-party loggers
    for name in ("turbovec", "sentence_transformers", "httpx", "urllib3", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # ── Rich console ────────────────────────────────────────────────
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        console = Console(highlight=False)
    except ImportError:
        print("Warning: 'rich' not installed. Output will be plain text.")
        console = None

    # ── Stats-only mode ─────────────────────────────────────────────
    if args.stats:
        from rag.vector_store import VectorStore
        store = VectorStore()
        stats = store.stats()
        if console:
            console.print(Panel.fit(
                f"Collection: {stats['collection']}\n"
                f"Persist Dir: {stats['persist_dir']}\n"
                f"Total Chunks: {stats['total_chunks']}",
                title="[Vector Store Stats]",
                border_style="cyan",
            ))
        else:
            print(f"Collection: {stats['collection']}")
            print(f"Persist Dir: {stats['persist_dir']}")
            print(f"Total Chunks: {stats['total_chunks']}")
        return

    # ── Validate root_dir ───────────────────────────────────────────
    if not args.root_dir:
        parser.error("root_dir is required (unless using --stats).")

    root = Path(args.root_dir).resolve()
    if not root.is_dir():
        print(f"Error: directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    # ── Run ingestion ───────────────────────────────────────────────
    from rag.bulk_ingestor import BulkIngestor

    if console:
        console.print(f"\n[bold cyan]>> Bulk RAG Ingestion[/bold cyan]")
        console.print(f"[dim]Root: {root}[/dim]")
        if args.force:
            console.print("[yellow]Mode: FORCE (re-indexing all)[/yellow]")
        if args.project:
            console.print(f"[yellow]Filter: only '{args.project}'[/yellow]")
        console.print()

    ingestor = BulkIngestor()
    report = ingestor.ingest_all(
        root_dir=root,
        force=args.force,
        project_filter=args.project,
    )

    # ── Print results ───────────────────────────────────────────────
    if console:
        table = Table(
            title="Ingestion Results",
            show_lines=True,
            border_style="cyan",
        )
        table.add_column("Project", style="bold white")
        table.add_column("Language", style="green")
        table.add_column("Framework", style="blue")
        table.add_column("Files", justify="right", style="yellow")
        table.add_column("Chunks", justify="right", style="magenta")
        table.add_column("Time", justify="right", style="dim")
        table.add_column("Status", justify="center")

        for r in report.projects:
            status = "OK" if not r.error else f"FAIL: {r.error[:30]}"
            table.add_row(
                r.name,
                r.language or "—",
                r.framework or "—",
                str(r.total_files),
                str(r.total_chunks),
                f"{r.time_seconds:.1f}s",
                status,
            )

        for name in report.skipped_projects:
            table.add_row(name, "-", "-", "-", "-", "-", "SKIP")

        console.print(table)
        console.print()
        console.print(Panel.fit(
            f"Projects indexed: {report.total_projects}\n"
            f"Projects skipped: {len(report.skipped_projects)}\n"
            f"Total files: {report.total_files}\n"
            f"Total chunks: {report.total_chunks}\n"
            f"Total time: {report.total_time_seconds:.1f}s",
            title="[Summary]",
            border_style="green",
        ))
    else:
        print(report.summary())


if __name__ == "__main__":
    main()
