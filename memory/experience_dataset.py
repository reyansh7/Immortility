"""Append-only experience dataset for future SFT/LoRA/DPO (export only)."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _dataset_path() -> Path:
    try:
        from core.repo_paths import experience_dataset_path

        return experience_dataset_path()
    except Exception:
        p = Path(".immortility")
        p.mkdir(parents=True, exist_ok=True)
        return p / "experience_dataset.jsonl"


def append_experience_record(record: dict[str, Any], *, min_confidence: float = 0.5) -> bool:
    """Append a high-quality interaction/lesson for later training export."""
    if not isinstance(record, dict):
        return False
    conf = float(record.get("confidence") or record.get("score") or 0)
    success = bool(record.get("success") or (conf >= min_confidence))
    if not success and conf < min_confidence:
        return False
    row = dict(record)
    row.setdefault("exported_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    path = _dataset_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.debug("Appended experience dataset record to %s", path)
    return True
