"""
Phase 2 tests for Immortility — Knowledge Engine.

Run with: pytest tests/test_phase2.py -v

Covers all 7 spec tests plus unit tests for every critical module.
"""

import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def temp_project(tmp_path):
    """Create a realistic temporary project for indexing tests."""
    # Python files
    (tmp_path / "main.py").write_text(
        'import os\nfrom fastapi import FastAPI\n\napp = FastAPI()\n\n'
        '@app.get("/")\ndef root():\n    """Root endpoint."""\n    return {"status": "ok"}\n\n'
        '@app.get("/health")\ndef health_check():\n    return {"healthy": True}\n',
        encoding="utf-8",
    )
    (tmp_path / "auth.py").write_text(
        'from fastapi import Depends, HTTPException\n\n'
        'class AuthManager:\n'
        '    """Handles authentication and JWT tokens."""\n\n'
        '    def __init__(self, secret_key: str):\n'
        '        self.secret_key = secret_key\n\n'
        '    def verify_token(self, token: str) -> dict:\n'
        '        """Verify a JWT token."""\n'
        '        return {"user": "test"}\n\n'
        '    def login(self, username: str, password: str) -> str:\n'
        '        """Authenticate user and return JWT."""\n'
        '        return "jwt_token"\n',
        encoding="utf-8",
    )
    (tmp_path / "middleware.py").write_text(
        'from auth import AuthManager\n\n'
        'def auth_middleware(request):\n'
        '    """Authentication middleware."""\n'
        '    token = request.headers.get("Authorization")\n'
        '    return AuthManager("secret").verify_token(token)\n',
        encoding="utf-8",
    )
    (tmp_path / "detect.py").write_text(
        'from ultralytics import YOLO\n\n'
        'class YOLODetector:\n'
        '    """YOLO object detection wrapper."""\n\n'
        '    def __init__(self, model_path: str = "yolov8n.pt"):\n'
        '        self.model = YOLO(model_path)\n\n'
        '    def detect(self, image):\n'
        '        """Run inference on an image."""\n'
        '        return self.model(image)\n',
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text(
        'from sqlalchemy import Column, Integer, String\n\n'
        'class User:\n'
        '    id = Column(Integer, primary_key=True)\n'
        '    username = Column(String)\n',
        encoding="utf-8",
    )

    # Requirements
    (tmp_path / "requirements.txt").write_text(
        "fastapi>=0.100.0\nuvicorn\nultralytics\nsqlalchemy\npyjwt\n",
        encoding="utf-8",
    )

    # README
    (tmp_path / "README.md").write_text(
        "# Inventory AI\n\n## Overview\nAI-powered inventory management.\n\n"
        "## Architecture\nFastAPI backend with YOLO detection and SQLAlchemy ORM.\n\n"
        "## Setup\n```bash\npip install -r requirements.txt\n```\n",
        encoding="utf-8",
    )

    # Subdirectory
    routers = tmp_path / "routers"
    routers.mkdir()
    (routers / "items.py").write_text(
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n'
        '@router.get("/items")\ndef list_items():\n    return []\n',
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def vector_db_dir(tmp_path):
    """Isolated vector DB directory for each test."""
    vdb = tmp_path / "vector_db"
    vdb.mkdir()
    return str(vdb)


@pytest.fixture
def memory_dir(tmp_path):
    """Isolated memory data directory."""
    md = tmp_path / "memory" / "data"
    md.mkdir(parents=True)
    return str(md)


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS
# ═══════════════════════════════════════════════════════════════════


# ── Chunker ─────────────────────────────────────────────────────────


class TestChunker:
    """Unit tests for the smart chunker."""

    def test_chunk_python_functions(self, tmp_path):
        """Python functions are extracted as individual chunks."""
        from rag.chunker import Chunker

        py_file = tmp_path / "example.py"
        py_file.write_text(
            "import os\n\ndef hello():\n    print('hi')\n\n"
            "def world():\n    print('world')\n",
            encoding="utf-8",
        )

        chunker = Chunker()
        chunks = chunker.chunk_file(py_file, project="test")

        assert len(chunks) >= 2
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) >= 2
        names = {c.function_name for c in func_chunks}
        assert "hello" in names
        assert "world" in names

    def test_chunk_python_class_and_methods(self, tmp_path):
        """Python classes and their methods are extracted."""
        from rag.chunker import Chunker

        py_file = tmp_path / "cls.py"
        py_file.write_text(
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        pass\n\n"
            "    def method_b(self):\n"
            "        pass\n",
            encoding="utf-8",
        )

        chunker = Chunker()
        chunks = chunker.chunk_file(py_file, project="test")

        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        method_chunks = [c for c in chunks if c.chunk_type == "method"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].class_name == "MyClass"
        assert len(method_chunks) >= 2

    def test_chunk_markdown_sections(self, tmp_path):
        """Markdown is split by headings."""
        from rag.chunker import Chunker

        md_file = tmp_path / "doc.md"
        md_file.write_text(
            "# Title\n\nIntro text.\n\n## Section One\n\nContent one.\n\n"
            "## Section Two\n\nContent two.\n",
            encoding="utf-8",
        )

        chunker = Chunker()
        chunks = chunker.chunk_file(md_file, project="test")

        assert len(chunks) >= 2
        section_names = {c.function_name for c in chunks if c.function_name}
        assert "Title" in section_names or "Section One" in section_names

    def test_chunk_metadata_preserved(self, tmp_path):
        """Each chunk carries filename, language, and project metadata."""
        from rag.chunker import Chunker

        py_file = tmp_path / "meta.py"
        py_file.write_text("def test():\n    pass\n", encoding="utf-8")

        chunker = Chunker()
        chunks = chunker.chunk_file(py_file, project="myproject")

        assert all(c.project == "myproject" for c in chunks)
        assert all(c.language == "python" for c in chunks)
        assert all(str(py_file) in c.filename for c in chunks)

    def test_unsupported_extension_returns_empty(self, tmp_path):
        """Files with unsupported extensions produce no chunks."""
        from rag.chunker import Chunker

        file = tmp_path / "image.png"
        file.write_bytes(b"\x89PNG")

        chunker = Chunker()
        chunks = chunker.chunk_file(file)
        assert chunks == []

    def test_chunk_json(self, tmp_path):
        """JSON files are handled as single module chunks."""
        from rag.chunker import Chunker

        json_file = tmp_path / "config.json"
        json_file.write_text('{"key": "value"}', encoding="utf-8")

        chunker = Chunker()
        chunks = chunker.chunk_file(json_file, project="test")
        assert len(chunks) >= 1
        assert chunks[0].language == "json"


# ── Vector Store ────────────────────────────────────────────────────


class TestVectorStore:
    """Unit tests for the ChromaDB vector store."""

    def test_add_and_search(self, vector_db_dir):
        """Add chunks and retrieve them by embedding similarity."""
        from rag.vector_store import VectorStore

        store = VectorStore(persist_dir=vector_db_dir, collection_name="test")

        # Add a simple document
        store.add(
            ids=["chunk1"],
            embeddings=[[0.1] * 384],
            documents=["def hello(): print('hi')"],
            metadatas=[{"filename": "test.py", "project": "test"}],
        )

        assert store.count() == 1

        results = store.search([0.1] * 384, n_results=1)
        assert len(results) == 1
        assert results[0]["id"] == "chunk1"
        assert "hello" in results[0]["document"]

    def test_delete_by_file(self, vector_db_dir):
        """Chunks can be deleted by filename."""
        from rag.vector_store import VectorStore

        store = VectorStore(persist_dir=vector_db_dir, collection_name="test_del")
        store.add(
            ids=["c1", "c2"],
            embeddings=[[0.1] * 384, [0.2] * 384],
            documents=["doc1", "doc2"],
            metadatas=[
                {"filename": "a.py", "project": "p"},
                {"filename": "b.py", "project": "p"},
            ],
        )

        assert store.count() == 2
        removed = store.delete_by_file("a.py")
        assert removed == 1
        assert store.count() == 1

    def test_persistence(self, vector_db_dir):
        """Data persists across VectorStore instances."""
        from rag.vector_store import VectorStore

        store1 = VectorStore(persist_dir=vector_db_dir, collection_name="persist")
        store1.add(
            ids=["p1"],
            embeddings=[[0.5] * 384],
            documents=["persistent doc"],
            metadatas=[{"filename": "f.py", "project": "test"}],
        )
        assert store1.count() == 1

        # Create a new instance pointing to the same directory
        store2 = VectorStore(persist_dir=vector_db_dir, collection_name="persist")
        assert store2.count() == 1


# ── Indexer ─────────────────────────────────────────────────────────


class TestIndexer:
    """Unit tests for the SHA-256 content-hashing indexer."""

    def test_index_file(self, tmp_path, vector_db_dir):
        """Indexing a file produces chunks in the vector store."""
        from rag.indexer import Indexer
        from rag.vector_store import VectorStore
        from rag.embeddings import EmbeddingModel

        py_file = tmp_path / "func.py"
        py_file.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        count = indexer.index_file(py_file, project="test")

        assert count >= 1
        assert store.count() >= 1

    def test_skip_unchanged_file(self, tmp_path, vector_db_dir):
        """Indexing the same unchanged file twice skips the second time."""
        from rag.indexer import Indexer
        from rag.vector_store import VectorStore

        py_file = tmp_path / "skip.py"
        py_file.write_text("x = 1\n", encoding="utf-8")

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)

        first = indexer.index_file(py_file, project="test")
        second = indexer.index_file(py_file, project="test")

        assert first >= 1
        assert second == 0  # Skipped — hash unchanged

    def test_reindex_on_change(self, tmp_path, vector_db_dir):
        """Modifying a file triggers re-indexing."""
        from rag.indexer import Indexer
        from rag.vector_store import VectorStore

        py_file = tmp_path / "change.py"
        py_file.write_text("def v1():\n    pass\n", encoding="utf-8")

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)

        indexer.index_file(py_file, project="test")

        # Modify the file
        py_file.write_text("def v2():\n    pass\n", encoding="utf-8")
        count = indexer.index_file(py_file, project="test")

        assert count >= 1  # Re-indexed with new content


# ── Project Indexer ─────────────────────────────────────────────────


class TestProjectIndexer:
    """Unit tests for the project indexer."""

    def test_index_project(self, temp_project, vector_db_dir):
        """Indexing a project discovers all files and detects the tech stack."""
        from rag.project_indexer import ProjectIndexer
        from rag.indexer import Indexer
        from rag.vector_store import VectorStore

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        pi = ProjectIndexer(indexer)

        info = pi.index_project(temp_project, "inventory-ai")

        assert info.name == "inventory-ai"
        assert info.total_files >= 5
        assert info.total_chunks >= 5
        assert "FastAPI" in info.framework
        assert "Python" in info.language
        assert store.count() >= 5

    def test_auto_detect_framework(self, temp_project, vector_db_dir):
        """Framework detection from requirements.txt."""
        from rag.project_indexer import ProjectIndexer
        from rag.indexer import Indexer
        from rag.vector_store import VectorStore

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        pi = ProjectIndexer(indexer)
        info = pi.index_project(temp_project, "test")

        assert "YOLO" in info.framework or "Ultralytics" in info.framework
        assert info.package_manager == "pip"


# ── Hybrid Search ───────────────────────────────────────────────────


class TestHybridSearch:
    """Unit tests for hybrid semantic + BM25 search."""

    def test_combined_search(self, temp_project, vector_db_dir):
        """Hybrid search returns results combining semantic and keyword scores."""
        from rag.hybrid_search import HybridSearch
        from rag.vector_store import VectorStore
        from rag.embeddings import EmbeddingModel
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        pi = ProjectIndexer(indexer)
        pi.index_project(temp_project, "test")

        search = HybridSearch(vector_store=store)
        results = search.search("authentication login JWT", n_results=5)

        assert len(results) >= 1
        filenames = [r.filename for r in results]
        assert any("auth" in f.lower() for f in filenames)


# ── Memory ──────────────────────────────────────────────────────────


class TestMemory:
    """Unit tests for the memory subsystem."""

    def test_conversation_memory(self, tmp_path):
        """Conversation memory stores and retrieves tasks."""
        from memory.conversation_memory import ConversationMemory

        mem = ConversationMemory(filepath=str(tmp_path / "conv.json"))
        mem.add_task("Built authentication module")
        mem.add_bug_fix("Fixed JWT expiry check")

        recent = mem.get_recent(n=10)
        assert len(recent) == 2
        assert recent[0].event_type == "task"
        assert "authentication" in recent[0].summary

    def test_project_memory(self, tmp_path):
        """Project memory stores and retrieves project profiles."""
        from memory.project_memory import ProjectMemory

        mem = ProjectMemory(filepath=str(tmp_path / "proj.json"))
        mem.remember_project({
            "name": "Inventory AI",
            "language": "Python",
            "framework": "FastAPI",
        })

        assert "Inventory AI" in mem.list_projects()
        proj = mem.get_project("Inventory AI")
        assert proj["framework"] == "FastAPI"

    def test_preference_memory(self, tmp_path):
        """Preference memory stores and retrieves user preferences."""
        from memory.preference_memory import PreferenceMemory

        mem = PreferenceMemory(filepath=str(tmp_path / "prefs.json"))
        mem.set_preference("language", "Python")
        mem.set_preference("editor", "VS Code")

        assert mem.get_preference("language") == "Python"
        assert mem.count() == 2

    def test_session_memory(self):
        """Session memory is ephemeral and clears correctly."""
        from memory.session_memory import SessionMemory

        mem = SessionMemory()
        mem.set("current_file", "main.py")
        assert mem.get("current_file") == "main.py"

        mem.clear()
        assert mem.get("current_file") is None

    def test_memory_manager_secret_filtering(self, tmp_path):
        """MemoryManager refuses to store secrets."""
        from memory.memory_manager import MemoryManager

        mgr = MemoryManager()
        mgr.preferences = __import__(
            "memory.preference_memory", fromlist=["PreferenceMemory"]
        ).PreferenceMemory(filepath=str(tmp_path / "prefs.json"))

        # Should reject
        result = mgr.store("preference", "api_key", "api_key=sk-1234567890abcdef1234567890")
        assert result is False

        # Should accept
        result = mgr.store("preference", "language", "Python")
        assert result is True


# ── Context Builder ─────────────────────────────────────────────────


class TestContextBuilder:
    """Unit tests for the context builder."""

    def test_builds_within_budget(self):
        """Context output stays within the token budget."""
        from knowledge.context_builder import ContextBuilder, ContextParts
        from rag.retriever import RetrievalResult

        builder = ContextBuilder(context_budget=500)

        chunks = [
            RetrievalResult(
                content="def hello():\n    return 'world'" * 20,
                filename="test.py",
                relevance_score=0.9,
                chunk_type="function",
                function_name="hello",
            )
            for _ in range(10)
        ]

        parts = ContextParts(
            retrieved_chunks=chunks,
            project_summary="Project: Test\nLanguage: Python",
            user_preferences="Editor: VS Code",
        )

        context = builder.build(parts)
        # 500 tokens * 4 chars ≈ 2000 chars max
        assert len(context) < 2500

    def test_empty_context(self):
        """Empty parts produce empty context."""
        from knowledge.context_builder import ContextBuilder, ContextParts

        builder = ContextBuilder()
        parts = ContextParts(retrieved_chunks=[])
        context = builder.build(parts)
        assert context == ""


# ── Context Ranker ──────────────────────────────────────────────────


class TestContextRanker:
    """Unit tests for the context ranker."""

    def test_deduplication(self):
        """Duplicate results are removed."""
        from knowledge.context_ranker import ContextRanker
        from rag.retriever import RetrievalResult

        ranker = ContextRanker()
        results = [
            RetrievalResult(
                content="def hello(): pass",
                filename="a.py",
                relevance_score=0.9,
                chunk_type="function",
            ),
            RetrievalResult(
                content="def hello(): pass",
                filename="a.py",
                relevance_score=0.8,
                chunk_type="function",
            ),
        ]

        ranked = ranker.rank(results)
        assert len(ranked) == 1

    def test_important_files_ranked_higher(self):
        """Files matching important patterns get higher scores."""
        from knowledge.context_ranker import ContextRanker
        from rag.retriever import RetrievalResult

        ranker = ContextRanker()
        results = [
            RetrievalResult(
                content="handler code",
                filename="utils/helper.py",
                relevance_score=0.7,
                chunk_type="function",
            ),
            RetrievalResult(
                content="main entry",
                filename="main.py",
                relevance_score=0.7,
                chunk_type="function",
            ),
        ]

        ranked = ranker.rank(results)
        # main.py should score higher due to importance
        assert ranked[0].filename == "main.py"


# ── Retrieval Cache ─────────────────────────────────────────────────


class TestRetrievalCache:
    """Unit tests for the retrieval cache."""

    def test_cache_hit_and_miss(self):
        """Cache returns stored values and None on miss."""
        from knowledge.retrieval_cache import RetrievalCache

        cache = RetrievalCache()
        cache.put("test query", "project", "cached_result")

        assert cache.get("test query", "project") == "cached_result"
        assert cache.get("other query", "project") is None

    def test_cache_invalidation(self):
        """File invalidation clears related cache entries."""
        from knowledge.retrieval_cache import RetrievalCache

        cache = RetrievalCache()
        # Store a result with file references
        results = [{"filename": "auth.py", "content": "auth code"}]
        cache.put("find auth", "proj", results)

        cache.invalidate_file("auth.py")
        assert cache.get("find auth", "proj") is None


# ═══════════════════════════════════════════════════════════════════
# SPEC TESTS (Tests 1–7 from the specification)
# ═══════════════════════════════════════════════════════════════════


class TestSpec1_ProjectAutoIndexed:
    """Test 1: Open a project → automatic indexing."""

    def test_project_opens_and_indexes(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        pi = ProjectIndexer(indexer)

        info = pi.index_project(temp_project, "Inventory AI")

        assert info.total_files >= 5
        assert info.total_chunks >= 5
        assert store.count() >= 5
        assert info.name == "Inventory AI"


class TestSpec2_FileRetrieval:
    """Test 2: 'Where is YOLO initialized?' → correct files retrieved."""

    def test_yolo_retrieval(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer
        from rag.retriever import Retriever

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        ProjectIndexer(indexer).index_project(temp_project, "test")

        retriever = Retriever(vector_store=store)
        results = retriever.retrieve("Where is YOLO initialized?", project="test")

        assert len(results) >= 1
        filenames = [r.filename for r in results]
        assert any("detect" in f.lower() for f in filenames)


class TestSpec3_ArchitectureContext:
    """Test 3: 'Explain my backend' → uses project context."""

    def test_backend_context(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer
        from rag.retriever import Retriever

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        ProjectIndexer(indexer).index_project(temp_project, "test")

        retriever = Retriever(vector_store=store)
        results = retriever.retrieve("Explain the backend architecture", project="test")

        assert len(results) >= 1
        all_content = " ".join(r.content for r in results)
        # Should retrieve FastAPI-related code
        assert "FastAPI" in all_content or "app" in all_content.lower()


class TestSpec4_AuthRetrieval:
    """Test 4: 'Fix authentication' → auth files found."""

    def test_auth_retrieval(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer
        from rag.retriever import Retriever

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        ProjectIndexer(indexer).index_project(temp_project, "test")

        retriever = Retriever(vector_store=store)
        results = retriever.retrieve("Fix authentication", project="test")

        assert len(results) >= 1
        filenames = [r.filename for r in results]
        assert any("auth" in f.lower() or "middleware" in f.lower() for f in filenames)


class TestSpec5_IncrementalReindex:
    """Test 5: Modify one file → only that file is re-indexed."""

    def test_incremental_reindex(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer

        store = VectorStore(persist_dir=vector_db_dir)
        indexer = Indexer(vector_store=store)
        pi = ProjectIndexer(indexer)

        # First full index
        info1 = pi.index_project(temp_project, "test")
        initial_chunks = info1.total_chunks

        # Second full index (no changes) — should skip everything
        info2 = pi.index_project(temp_project, "test")
        assert info2.total_chunks == 0  # All skipped

        # Modify one file
        auth_file = temp_project / "auth.py"
        auth_file.write_text(
            auth_file.read_text(encoding="utf-8") + "\ndef new_function():\n    pass\n",
            encoding="utf-8",
        )

        # Third index — should only re-index auth.py
        info3 = pi.index_project(temp_project, "test")
        assert info3.total_chunks >= 1  # At least auth.py re-indexed
        assert info3.total_chunks < initial_chunks  # Not everything


class TestSpec6_Persistence:
    """Test 6: Restart → vector database persists, no full rebuild."""

    def test_vector_db_persists(self, temp_project, vector_db_dir):
        from rag.vector_store import VectorStore
        from rag.indexer import Indexer
        from rag.project_indexer import ProjectIndexer

        store1 = VectorStore(persist_dir=vector_db_dir)
        indexer1 = Indexer(vector_store=store1)
        ProjectIndexer(indexer1).index_project(temp_project, "test")
        count_before = store1.count()
        assert count_before >= 5

        # "Restart" — create new instances
        store2 = VectorStore(persist_dir=vector_db_dir)
        assert store2.count() == count_before  # Data persisted


class TestSpec7_ProjectMemory:
    """Test 7: 'Remember this project' → stored in Project Memory."""

    def test_remember_project(self, temp_project, tmp_path):
        from memory.project_memory import ProjectMemory
        from rag.project_indexer import ProjectInfo

        mem = ProjectMemory(filepath=str(tmp_path / "proj.json"))

        info = ProjectInfo(
            name="Inventory AI",
            path=str(temp_project),
            language="Python",
            framework="FastAPI, YOLO",
            total_files=7,
            total_chunks=25,
        )
        mem.remember_project(info.to_dict())

        assert "Inventory AI" in mem.list_projects()
        stored = mem.get_project("Inventory AI")
        assert stored["framework"] == "FastAPI, YOLO"
        assert stored["language"] == "Python"
