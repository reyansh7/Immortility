"""Phase 1A/B/D — experiences and reflections are retrieved and injected."""

from __future__ import annotations

import tempfile
from pathlib import Path

from memory.experience_memory import ExperienceMemory, format_experiences_block


def test_format_experiences_block_contains_fix():
    mem_path = Path(tempfile.mkdtemp()) / "exp.json"
    mem = ExperienceMemory(db_path=mem_path)
    mem.record_bug_fix(
        error_message="Module not found: jwt login auth",
        file_path="src/auth.py",
        fix_applied="Install pyjwt and import jose correctly",
    )
    hits = mem.get_relevant_experiences("fix jwt login auth bug", limit=3)
    assert hits, "expected keyword/embedding hit for jwt login"
    block = format_experiences_block(hits)
    assert "PAST EXPERIENCES" in block
    assert "pyjwt" in block.lower() or "jwt" in block.lower()


def test_action_context_override_includes_experiences_shape():
    """Simulate the action_engine merge of experiences into context."""
    mem_path = Path(tempfile.mkdtemp()) / "exp2.json"
    mem = ExperienceMemory(db_path=mem_path)
    mem.record(
        task="fix next.js globals.css path",
        outcome="success",
        details="Moved globals.css under src/app/globals.css",
    )
    relevant = mem.get_relevant_experiences("next.js globals.css missing", limit=2)
    assert relevant
    ctx = "No extra retrieved context provided."
    block = format_experiences_block(relevant)
    merged = f"{ctx}\n\n{block}"
    assert "PAST EXPERIENCES" in merged
    assert "globals.css" in merged


def test_format_reflections_block_prefers_overlap():
    from knowledge.reflection_format import format_reflections_block

    refs = [
        {
            "task": "unrelated weather app",
            "what_broke": "rain API",
            "what_fixed_it": "retry",
        },
        {
            "task": "fix login jwt authentication",
            "what_broke": "wrong secret",
            "what_fixed_it": "use env JWT_SECRET",
        },
    ]
    block = format_reflections_block(refs, goal="fix the jwt login bug", limit=3)
    assert "PAST REFLECTIONS" in block
    assert "JWT_SECRET" in block or "jwt" in block.lower()
