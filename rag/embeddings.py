"""Local embedding model using BAAI/bge-small-en-v1.5.

Loads the model lazily on first use and caches it permanently
for the lifetime of the process. Thread-safe singleton ensures
the model is never loaded more than once.
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingModel:
    """Singleton embedding model backed by sentence-transformers.

    The model is loaded lazily on the first ``encode()`` or
    ``encode_query()`` call and cached permanently.  All subsequent
    calls reuse the same model instance, avoiding redundant disk I/O
    and GPU/CPU allocation.
    """

    _instance: Optional["EmbeddingModel"] = None
    _lock: threading.Lock = threading.Lock()

    MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
    DIMENSION: int = 384

    def __new__(cls) -> "EmbeddingModel":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._model = None  # type: ignore[attr-defined]
                    cls._instance = inst
        return cls._instance

    # ── Model loading ───────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load the model on first call (double-checked locking)."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            # pyrefly: ignore [missing-import]
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s …", self.MODEL_NAME)
            self._model = SentenceTransformer(self.MODEL_NAME)
            logger.info(
                "Embedding model loaded (dimension=%d).", self.DIMENSION
            )

    # ── Public API ──────────────────────────────────────────────────

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts into normalised embedding vectors.

        Args:
            texts: List of passages / code chunks to embed.

        Returns:
            List of float vectors, one per input text.
        """
        if not texts:
            return []
        self._ensure_loaded()
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def encode_query(self, query: str) -> list[float]:
        """Encode a search query with a retrieval-optimised prefix.

        BGE models benefit from a short instruction prefix when the
        text is a *query* rather than a *passage*.

        Args:
            query: Natural-language search query.

        Returns:
            A single float vector.
        """
        self._ensure_loaded()
        prefixed = (
            "Represent this sentence for searching relevant passages: "
            + query
        )
        embedding = self._model.encode(
            prefixed,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    # ── Testing helper ──────────────────────────────────────────────

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton — used in tests only."""
        with cls._lock:
            cls._instance = None
