"""TurboVec VectorStore API + project allowlist filtering."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def store(tmp_path):
    from rag.vector_store import VectorStore

    s = VectorStore(persist_dir=str(tmp_path), collection_name="unit")
    yield s
    s.reset()


def _emb(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(384).astype(np.float32)
    v /= np.linalg.norm(v) + 1e-9
    return v.tolist()


def test_add_search_delete_project(store):
    store.add(
        ids=["a", "b", "c"],
        embeddings=[_emb(1), _emb(2), _emb(3)],
        documents=["alpha code", "beta code", "gamma other"],
        metadatas=[
            {"filename": "a.py", "project": "p1", "start_line": 1},
            {"filename": "b.py", "project": "p1", "start_line": 1},
            {"filename": "c.py", "project": "p2", "start_line": 1},
        ],
    )
    assert store.count() == 3
    hits = store.search(_emb(1), n_results=2)
    assert len(hits) >= 1
    assert "document" in hits[0] and "distance" in hits[0]

    filtered = store.search(_emb(1), n_results=5, where={"project": "p2"})
    assert filtered
    assert all(h["metadata"].get("project") == "p2" for h in filtered)

    deleted = store.delete_by_project("p1")
    assert deleted == 2
    assert store.count() == 1


def test_upsert_and_persistence(tmp_path):
    from rag.vector_store import VectorStore

    s1 = VectorStore(persist_dir=str(tmp_path), collection_name="persist")
    s1.add(
        ids=["x1"],
        embeddings=[_emb(9)],
        documents=["first"],
        metadatas=[{"filename": "x.py", "project": "z"}],
    )
    s1.add(
        ids=["x1"],
        embeddings=[_emb(10)],
        documents=["second"],
        metadatas=[{"filename": "x.py", "project": "z"}],
    )
    assert s1.count() == 1
    docs = s1.get_all_documents()
    assert docs[0]["document"] == "second"

    # Reload from disk
    s2 = VectorStore(persist_dir=str(tmp_path), collection_name="persist")
    assert s2.count() == 1
    assert s2.get_all_documents()[0]["document"] == "second"


def test_stats_backend(store):
    st = store.stats()
    assert st["backend"] == "turbovec"
    assert st["total_chunks"] == 0
