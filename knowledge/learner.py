"""Self-learning intake — store facts, scans, and web research into TurboVec.

Immortility improves by writing durable notes into the ``documentation``
collection so later retrieval can use them (not just ephemeral chat history).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def remember_text(
    text: str,
    *,
    source: str = "learned",
    kind: str = "note",
    project: str = "immortility-memory",
) -> dict[str, Any]:
    """Embed a free-form note into the documentation vector store.

    Returns a small status dict. Safe to call from HUD / CLI / research paths.
    """
    body = (text or "").strip()
    if len(body) < 20:
        return {"status": "skipped", "reason": "too_short"}

    with _LOCK:
        try:
            from rag.embeddings import EmbeddingModel
            from rag.vector_store import VectorStore

            store = VectorStore(collection_name="documentation")
            embedder = EmbeddingModel()

            # Stable id so re-learning the same snapshot upserts instead of duplicating
            digest = hashlib.sha256(
                f"{source}|{kind}|{body[:2000]}".encode("utf-8", errors="replace")
            ).hexdigest()[:24]
            chunk_id = f"learn:{kind}:{digest}"

            # Keep chunks usable for retrieval (split long notes)
            chunks: list[str] = []
            step = 1200
            for i in range(0, len(body), step):
                part = body[i : i + step].strip()
                if part:
                    chunks.append(part)
            if not chunks:
                return {"status": "skipped", "reason": "empty"}

            ids = [f"{chunk_id}:{i}" for i in range(len(chunks))]
            metas = [
                {
                    "source": source,
                    "kind": kind,
                    "project": project,
                    "filename": f"{kind}.md",
                    "learned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                for _ in chunks
            ]
            vectors = embedder.encode(chunks)
            store.add(ids=ids, embeddings=vectors, documents=chunks, metadatas=metas)
            logger.info(
                "Learned %d chunk(s) kind=%s source=%s", len(chunks), kind, source
            )
            return {"status": "ok", "chunks": len(chunks), "kind": kind, "source": source}
        except Exception as exc:
            logger.warning("remember_text failed: %s", exc)
            return {"status": "error", "message": str(exc)}


def remember_desktop_inventory(compact_text: str) -> dict[str, Any]:
    """Persist a Desktop/Projects inventory snapshot for future RAG."""
    return remember_text(
        compact_text,
        source="desktop_scan",
        kind="desktop_inventory",
        project="desktop-projects",
    )


def remember_web_research(query: str, summary: str, sources: list[str] | None = None) -> dict[str, Any]:
    """Persist web-research findings so Immortility can reuse them later."""
    src_line = ", ".join((sources or [])[:8])
    body = (
        f"Web research query: {query}\n"
        f"Sources: {src_line or 'n/a'}\n\n"
        f"{(summary or '').strip()}"
    )
    return remember_text(
        body,
        source="web_research",
        kind="web_research",
        project="web-knowledge",
    )
