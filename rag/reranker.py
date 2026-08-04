"""Optional CrossEncoder reranking for hybrid retrieval.

Disabled by default (``RERANK_ENABLED=0``) so voice + LLM VRAM stays free.
Enable with ``RERANK_ENABLED=1`` — runs on CPU via sentence-transformers.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"


def rerank_enabled() -> bool:
    raw = (os.environ.get("RERANK_ENABLED") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class CrossEncoderReranker:
    """Lazy CPU CrossEncoder — no-op when disabled or model missing."""

    _instance: CrossEncoderReranker | None = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = (
            model_name
            or (os.environ.get("RERANK_MODEL") or DEFAULT_RERANK_MODEL).strip()
        )
        self._model: Any = None
        self._load_failed = False

    @classmethod
    def get(cls) -> CrossEncoderReranker:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = CrossEncoderReranker()
        return cls._instance

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, device="cpu")
            logger.info("Reranker ready model=%s device=cpu", self._model_name)
            return True
        except Exception as exc:
            self._load_failed = True
            logger.warning("Reranker unavailable (%s) — skipping", exc)
            return False

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        top_k: int | None = None,
        text_attr: str = "content",
        score_attr: str = "score",
    ) -> list[Any]:
        """Rerank objects that expose ``content`` (or ``text_attr``).

        Works with ``SearchResult`` / ``RetrievalResult``-like objects.
        Returns the same objects, reordered, with ``score`` overwritten when present.
        """
        items = list(candidates)
        if not items or not query.strip():
            return items
        if not rerank_enabled():
            return items[:top_k] if top_k else items
        if not self._ensure_loaded():
            return items[:top_k] if top_k else items

        pairs: list[list[str]] = []
        for item in items:
            if isinstance(item, dict):
                text = str(item.get(text_attr) or item.get("document") or "")
            else:
                text = str(getattr(item, text_attr, "") or "")
            pairs.append([query, text[:4000]])

        try:
            scores = self._model.predict(pairs)
        except Exception as exc:
            logger.warning("Rerank predict failed: %s", exc)
            return items[:top_k] if top_k else items

        ranked = sorted(
            zip(items, scores, strict=False),
            key=lambda x: float(x[1]),
            reverse=True,
        )
        out: list[Any] = []
        for item, score in ranked:
            if isinstance(item, dict):
                item = {**item, score_attr: float(score)}
            else:
                try:
                    setattr(item, score_attr, float(score))
                except Exception:
                    pass
            out.append(item)
        if top_k is not None:
            out = out[: max(1, int(top_k))]
        return out


def rerank_results(query: str, candidates: Sequence[Any], top_k: int | None = None) -> list[Any]:
    """Module-level helper used by KnowledgeEngine."""
    return CrossEncoderReranker.get().rerank(
        query,
        candidates,
        top_k=top_k,
        score_attr="relevance_score",
    )
