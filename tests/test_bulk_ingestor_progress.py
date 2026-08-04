"""Bulk ingest: TurboVec skip + line-by-line progress."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rag.bulk_ingestor import BulkIngestor, ProjectIngestResult


def test_is_already_indexed_uses_count_by_project():
    store = MagicMock()
    store.count_by_project.return_value = 42
    embedder = MagicMock()
    ingestor = BulkIngestor(vector_store=store, embedding_model=embedder)
    assert ingestor.is_already_indexed("stocks_app") is True
    store.count_by_project.assert_called_with("stocks_app")

    store.count_by_project.return_value = 0
    assert ingestor.is_already_indexed("new_proj") is False


def test_ingest_paths_skips_and_logs_progress(tmp_path):
    a = tmp_path / "AlreadyIndexed"
    b = tmp_path / "NeedsIndex"
    a.mkdir()
    b.mkdir()
    (b / "main.py").write_text("print(1)\n", encoding="utf-8")

    store = MagicMock()

    def count_by_project(name: str) -> int:
        return 100 if name == "AlreadyIndexed" else 0

    store.count_by_project.side_effect = count_by_project
    embedder = MagicMock()
    logs: list[str] = []

    ingestor = BulkIngestor(
        vector_store=store,
        embedding_model=embedder,
        progress=logs.append,
    )
    # Avoid real embedding / file walk for NeedsIndex
    ingestor.ingest_project = MagicMock(  # type: ignore[method-assign]
        return_value=ProjectIngestResult(
            name="NeedsIndex",
            path=str(b),
            total_files=1,
            total_chunks=3,
            time_seconds=0.1,
        )
    )

    report = ingestor.ingest_paths([a, b], force=False)

    assert "AlreadyIndexed" in report.skipped_projects
    assert report.total_projects == 1
    assert any("SKIP" in m and "AlreadyIndexed" in m for m in logs)
    assert any("Bulk index: 2" in m for m in logs)
    ingestor.ingest_project.assert_called_once()


def test_ingest_project_emits_per_file_progress(tmp_path):
    proj = tmp_path / "Tiny"
    proj.mkdir()
    (proj / "a.py").write_text("a=1\n", encoding="utf-8")
    (proj / "b.py").write_text("b=2\n", encoding="utf-8")

    store = MagicMock()
    store.count_by_project.return_value = 0
    embedder = MagicMock()
    logs: list[str] = []

    ingestor = BulkIngestor(
        vector_store=store,
        embedding_model=embedder,
        progress=logs.append,
    )

    # Patch project indexer to call on_progress like the real one
    def fake_index(path, name, *, on_progress=None):
        files = [proj / "a.py", proj / "b.py"]
        if on_progress:
            on_progress(0, len(files), Path(path), 0)
            on_progress(1, len(files), files[0], 2)
            on_progress(2, len(files), files[1], 1)
        info = MagicMock()
        info.total_files = 2
        info.total_chunks = 3
        info.language = "python"
        info.framework = ""
        return info

    ingestor._project_indexer.index_project = fake_index  # noqa: SLF001
    result = ingestor.ingest_project(proj, project_index=1, project_total=1)

    assert result.total_chunks == 3
    assert any("START" in m for m in logs)
    assert any("[1/2]" in m and "a.py" in m for m in logs)
    assert any("[2/2]" in m and "b.py" in m for m in logs)
    assert any("DONE" in m for m in logs)
