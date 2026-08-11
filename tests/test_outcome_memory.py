"""Phase 1C — outcome scoring and lesson retrieval."""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory.outcome_memory import (
    OutcomeMemory,
    format_lessons_block,
    score_previous_outcome,
)


def test_score_explicit_positive_negative():
    assert score_previous_outcome("thanks", "what is jwt")[0] == 1.0
    score, corr = score_previous_outcome(
        "wrong, I meant OAuth only login", "add a login page"
    )
    assert score == -1.0
    assert "oauth" in corr.lower() or "OAuth" in corr or "login" in corr.lower()


def test_score_rephrase_is_negative():
    score, _ = score_previous_outcome(
        "how do I fix the jwt login bug please",
        "fix the jwt login bug",
    )
    assert score == -0.5


def test_record_and_retrieve_lessons():
    db = Path(tempfile.mkdtemp()) / "outcomes.db"
    mem = OutcomeMemory(db_path=db)
    mem.record(
        query="add a login page with jwt",
        route="PROJECT",
        response="Created email/password form",
        score=-1.0,
        correction="user wanted OAuth-only login",
        tags=["login", "jwt", "auth"],
    )
    lessons = mem.get_relevant_lessons("fix login jwt authentication", limit=3)
    assert lessons
    assert lessons[0].score < 0
    block = format_lessons_block(lessons)
    assert "PAST MISTAKES" in block
    assert "OAuth" in block or "oauth" in block.lower()


def test_pending_score_flow():
    db = Path(tempfile.mkdtemp()) / "outcomes2.db"
    mem = OutcomeMemory(db_path=db)
    mem.set_pending("explain stocks_app", "CHAT", "It is a trading dashboard...")
    scored = mem.score_and_record_previous("wrong, I meant the Portfolio project")
    assert scored == -1.0
    lessons = mem.get_relevant_lessons("stocks_app portfolio", limit=2)
    assert any(l.correction for l in lessons)
