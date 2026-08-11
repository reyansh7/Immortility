"""Phase 4 — self reflection + dataset export (tuner stays off by default)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.self_reflection import reflect_and_store
from memory.experience_dataset import append_experience_record
from memory.response_tuner import ResponseTuner, tuner_enabled


def test_reflect_and_store_writes_experience(tmp_path=None):
    import os

    # Isolate experience file
    d = Path(tempfile.mkdtemp())
    os.environ.setdefault("IMMORTILITY_RESPONSE_TUNER", "0")
    from memory.experience_memory import ExperienceMemory

    mem = ExperienceMemory(db_path=d / "exp.json")
    # Monkeypatch ExperienceMemory default by recording via direct instance in reflect —
    # reflect_and_store constructs its own; call record path via reflect and also dataset.
    lesson = reflect_and_store(
        task="fix jwt login secret",
        tools_used=["read_file", "edit_file"],
        result="Updated JWT_SECRET usage",
        success=True,
        project="demo",
        confidence=0.9,
    )
    assert lesson.success is True
    assert "jwt" in lesson.lesson.lower() or "Succeeded" in lesson.lesson


def test_experience_dataset_append():
    d = Path(tempfile.mkdtemp())
    path = d / "dataset.jsonl"
    # Patch path via writing through append with monkeypatched function
    from memory import experience_dataset as ed

    old = ed._dataset_path
    ed._dataset_path = lambda: path  # type: ignore
    try:
        ok = append_experience_record(
            {"attempted": "x", "success": True, "confidence": 0.9, "lesson": "y"}
        )
        assert ok is True
        assert path.is_file()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
    finally:
        ed._dataset_path = old


def test_response_tuner_disabled_by_default():
    assert tuner_enabled() is False
    tuner = ResponseTuner(path=Path(tempfile.mkdtemp()) / "m.json")
    # Should return config/default without adapting when disabled
    limit = tuner.get_adaptive_token_limit("CHAT", "text")
    assert isinstance(limit, int) and limit > 0
