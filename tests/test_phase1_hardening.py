"""Phase 1 hardening: secrets denylist, repo-aware tests, hash incremental index."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.repo_detector import RepositoryDetector
from editing.verifier import Verifier
from rag.index_state_db import IndexStateDB
from rag.indexer import Indexer
from rag.project_indexer import ProjectIndexer
from rag.security_filters import (
    chunk_contains_secret,
    is_denied_file,
    should_index_path,
)


def test_env_files_are_denied():
    assert is_denied_file(".env") is True
    assert is_denied_file("app/.env.local") is True
    assert is_denied_file("secrets/credentials.json") is True
    assert is_denied_file("keys/id_rsa") is True
    assert is_denied_file("cert.pem") is True
    assert is_denied_file("src/main.py") is False


def test_project_indexer_skips_env_and_noise(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("export default 1", encoding="utf-8")
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "cache.js").write_text("x=1", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=abc", encoding="utf-8")
    (tmp_path / ".env.local").write_text("KEY=xyz", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")

    indexer = ProjectIndexer(indexer=MagicMock())
    files = indexer._walk(tmp_path)
    rels = {str(f.relative_to(tmp_path)).replace("\\", "/") for f in files}

    assert "src/app.py" in rels
    assert ".env" not in rels
    assert ".env.local" not in rels
    assert not any(r.startswith("node_modules") for r in rels)
    assert not any(r.startswith(".next") for r in rels)


def test_secret_scanner_blocks_chunks():
    assert chunk_contains_secret('OPENAI_API_KEY = "sk-abcdefghijklmnopqrstuvwxyz"') is True
    assert chunk_contains_secret("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345") is True
    assert chunk_contains_secret("-----BEGIN PRIVATE KEY-----\nMIIE") is True
    assert chunk_contains_secret("def add(a, b):\n    return a + b\n") is False


def test_indexer_skips_env_file_entirely(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PASSWORD=hunter2\n", encoding="utf-8")
    store = MagicMock()
    embedder = MagicMock()
    embedder.encode.return_value = [[0.1] * 8]
    idx = Indexer(
        vector_store=store,
        embedding_model=embedder,
        state_file=tmp_path / "index_state.db",
    )
    assert idx.index_file(env, project="t") == 0
    store.add.assert_not_called()


def test_indexer_skips_secret_bearing_chunk(tmp_path):
    src = tmp_path / "config.py"
    src.write_text(
        'API_KEY = "abcdefghijklmnopqrstuvwxyz12"\n\ndef ok():\n    return 1\n',
        encoding="utf-8",
    )
    store = MagicMock()
    embedder = MagicMock()
    embedder.encode.return_value = [[0.1] * 8]

    # Force a single chunk containing the secret
    fake_chunk = MagicMock()
    fake_chunk.content = 'API_KEY = "abcdefghijklmnopqrstuvwxyz12"'
    fake_chunk.to_search_text.return_value = fake_chunk.content
    fake_chunk.chunk_id = "c1"
    fake_chunk.filename = "config.py"
    fake_chunk.language = "python"
    fake_chunk.chunk_type = "module"
    fake_chunk.project = "t"
    fake_chunk.start_line = 1
    fake_chunk.end_line = 1
    fake_chunk.class_name = ""
    fake_chunk.function_name = ""
    fake_chunk.imports = []

    chunker = MagicMock()
    chunker.chunk_file.return_value = [fake_chunk]

    idx = Indexer(
        vector_store=store,
        embedding_model=embedder,
        chunker=chunker,
        state_file=tmp_path / "index_state.db",
    )
    n = idx.index_file(src, project="t")
    assert n == 0
    store.add.assert_not_called()


def test_repo_detector_python(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    profile = RepositoryDetector.detect(tmp_path)
    assert profile.kind == "python"
    assert profile.test_command[:3] == ["python", "-m", "pytest"]


def test_repo_detector_node(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest"}}',
        encoding="utf-8",
    )
    profile = RepositoryDetector.detect(tmp_path)
    assert profile.kind == "node"
    assert profile.test_command[0] == "npm"


def test_repo_detector_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n', encoding="utf-8")
    profile = RepositoryDetector.detect(tmp_path)
    assert profile.kind == "rust"
    assert profile.test_command == ["cargo", "test"]


def test_verify_project_tests_uses_detector(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo ok"}}',
        encoding="utf-8",
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        ok, msg = Verifier.verify_project_tests(tmp_path)
    assert ok is True
    assert "npm" in msg
    assert mock_run.call_args.kwargs["cwd"] == str(tmp_path.resolve())


def test_unchanged_file_skips_reindex(tmp_path):
    src = tmp_path / "math.py"
    src.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    store = MagicMock()
    embedder = MagicMock()
    embedder.encode.return_value = [[0.1] * 8]

    fake_chunk = MagicMock()
    fake_chunk.content = "def add(a, b):\n    return a + b\n"
    fake_chunk.to_search_text.return_value = fake_chunk.content
    fake_chunk.chunk_id = "math:add"
    fake_chunk.filename = "math.py"
    fake_chunk.language = "python"
    fake_chunk.chunk_type = "function"
    fake_chunk.project = "t"
    fake_chunk.start_line = 1
    fake_chunk.end_line = 2
    fake_chunk.class_name = ""
    fake_chunk.function_name = "add"
    fake_chunk.imports = []

    chunker = MagicMock()
    chunker.chunk_file.return_value = [fake_chunk]

    idx = Indexer(
        vector_store=store,
        embedding_model=embedder,
        chunker=chunker,
        state_file=tmp_path / "index_state.db",
    )
    first = idx.index_file(src, project="t")
    assert first == 1
    assert store.add.call_count == 1

    second = idx.index_file(src, project="t")
    assert second == 0
    assert store.add.call_count == 1  # unchanged → no re-embed

    # Hash in SQLite matches file
    db = IndexStateDB(tmp_path / "index_state.db")
    entry = db.get(str(src.resolve()))
    assert entry is not None
    assert entry.sha256 == hashlib.sha256(src.read_bytes()).hexdigest()

    # Change file → reindex
    src.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    third = idx.index_file(src, project="t")
    assert third == 1
    assert store.add.call_count == 2


def test_should_index_path_blocks_node_modules():
    assert should_index_path("proj/node_modules/pkg/index.js") is False
    assert should_index_path("proj/src/index.js") is True
