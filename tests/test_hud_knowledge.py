"""HUD knowledge routing: /open and index intents → TurboVec, not listings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.hud_knowledge import (
    handle_hud_knowledge,
    wants_index_or_ingest,
)


def test_wants_index_phrases():
    assert wants_index_or_ingest("/open C:\\Users\\x\\Desktop\\Projects")
    assert wants_index_or_ingest("index projects in my desktop yourself and read")
    assert wants_index_or_ingest("read the projects")
    assert wants_index_or_ingest("understand all my projects")
    assert wants_index_or_ingest("ingest my desktop projects")
    assert not wants_index_or_ingest("list all the folders in the projects folder")
    assert not wants_index_or_ingest("open youtube")


def test_open_immortility_indexes_not_vscode(tmp_path):
    proj = tmp_path / "immortility1"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)\n", encoding="utf-8")

    fake_result = MagicMock()
    fake_result.name = "immortility1"
    fake_result.total_files = 1
    fake_result.total_chunks = 3
    fake_result.language = "python"
    fake_result.framework = ""
    fake_result.error = ""
    fake_result.skipped = False

    with patch("knowledge.engine.KnowledgeEngine") as KE:
        store = MagicMock()
        store.count_by_project.return_value = 0
        KE.return_value._vector_store = store
        KE.return_value._embedding_model = MagicMock()
        with patch("rag.bulk_ingestor.BulkIngestor") as BI:
            BI.return_value.ingest_project.return_value = fake_result
            with patch(
                "tools.hud_knowledge._estimate_eta_seconds",
                return_value=(30.0, 10),
            ):
                reply = handle_hud_knowledge(f"/open {proj}")

    assert reply is not None
    assert "Indexed" in reply or "chunks" in reply.lower()
    assert "VS Code" not in reply
    BI.return_value.ingest_project.assert_called_once()


def test_open_projects_folder_bulk(tmp_path):
    root = tmp_path / "Projects"
    root.mkdir()
    a = root / "AppA"
    b = root / "AppB"
    a.mkdir()
    b.mkdir()
    (a / "a.py").write_text("x=1\n", encoding="utf-8")
    (b / "b.py").write_text("y=2\n", encoding="utf-8")

    from rag.bulk_ingestor import ProjectIngestResult

    fake_report = MagicMock()
    fake_report.total_projects = 2
    fake_report.total_files = 2
    fake_report.total_chunks = 4
    fake_report.skipped_projects = []
    fake_report.projects = [
        ProjectIngestResult(
            name="AppA",
            path=str(a),
            total_files=1,
            total_chunks=2,
            time_seconds=0.5,
            language="python",
        ),
        ProjectIngestResult(
            name="AppB",
            path=str(b),
            total_files=1,
            total_chunks=2,
            time_seconds=0.4,
            language="python",
        ),
    ]

    with patch("knowledge.engine.KnowledgeEngine") as KE:
        store = MagicMock()
        store.count.return_value = 4
        KE.return_value._vector_store = store
        KE.return_value._embedding_model = MagicMock()
        KE.return_value.get_routing_context.return_value = ""
        with patch("rag.bulk_ingestor.BulkIngestor") as BI:
            BI.return_value.ingest_paths.return_value = fake_report
            with patch(
                "tools.hud_knowledge._estimate_eta_seconds",
                return_value=(90.0, 20),
            ):
                with patch(
                    "core.desktop_scanner.summarize_folder",
                    return_value="python app",
                ):
                    with patch(
                        "core.desktop_scanner.projects_dir",
                        return_value=tmp_path / "nope",
                    ):
                        reply = handle_hud_knowledge(f"/open {root}")

    assert reply is not None
    assert "ETA" in reply or "Finished" in reply
    assert "Finished" in reply
    BI.return_value.ingest_paths.assert_called_once()
    args, kwargs = BI.return_value.ingest_paths.call_args
    assert len(args[0]) == 2


def test_hud_agent_routes_open_to_knowledge():
    from tools.hud_agent import handle_hud_request

    with patch(
        "tools.hud_knowledge.handle_hud_knowledge",
        return_value="INDEXED_OK",
    ) as hk:
        with patch(
            "tools.hud_knowledge.wants_index_or_ingest",
            return_value=True,
        ):
            out = handle_hud_request(
                "/open C:\\Users\\reyan\\OneDrive\\Desktop\\Projects"
            )
    assert out == "INDEXED_OK"
    hk.assert_called_once()
