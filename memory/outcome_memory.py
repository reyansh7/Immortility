"""Tracks prompt→outcome pairs for self-learning feedback loops."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    try:
        from core.repo_paths import outcomes_db_path

        return outcomes_db_path()
    except Exception:
        p = Path(".immortility")
        p.mkdir(parents=True, exist_ok=True)
        return p / "outcomes.db"


@dataclass
class Outcome:
    query_hash: str
    query_summary: str
    route: str
    response_summary: str
    score: float
    tags: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    correction: str = ""


@dataclass
class PendingOutcome:
    query: str
    route: str
    response_summary: str


_POSITIVE = frozenset({
    "yes", "y", "correct", "perfect", "thanks", "thank you", "that works",
    "great", "awesome", "good", "ok", "okay", "works", "exactly",
})
_NEGATIVE_PREFIXES = (
    "no,", "no ", "wrong", "that's not", "thats not", "not what i",
    "try again", "incorrect", "nope", "i meant", "i said",
)


def score_previous_outcome(current_input: str, last_query: str) -> tuple[float, str]:
    """Return (score, correction_text) for the previous turn based on this message."""
    low = (current_input or "").strip().lower().rstrip(".!?,;: ")
    if not low:
        return 0.0, ""

    if low in _POSITIVE or low.startswith("thanks") or low.startswith("thank you"):
        return 1.0, ""

    for pref in _NEGATIVE_PREFIXES:
        if low.startswith(pref) or pref.strip() in low[:40]:
            # Capture correction after "i meant" / "wrong,"
            correction = ""
            m = re.search(
                r"(?:i meant|not that[, ]*|wrong[, ]*|instead)\s*(.+)",
                current_input,
                re.I,
            )
            if m:
                correction = m.group(1).strip()[:400]
            return -1.0, correction

    if last_query and _is_rephrase(current_input, last_query):
        return -0.5, ""

    return 0.0, ""


def _is_rephrase(a: str, b: str) -> bool:
    """Heuristic: substantial token overlap but not identical."""
    ta = {t for t in re.findall(r"[a-z0-9_]+", (a or "").lower()) if len(t) > 2}
    tb = {t for t in re.findall(r"[a-z0-9_]+", (b or "").lower()) if len(t) > 2}
    if not ta or not tb:
        return False
    if a.strip().lower() == b.strip().lower():
        return True
    inter = len(ta & tb)
    union = len(ta | tb)
    overlap = inter / union if union else 0.0
    return overlap >= 0.55 and a.strip().lower() != b.strip().lower()


def format_lessons_block(outcomes: list[Outcome], limit: int = 3) -> str:
    if not outcomes:
        return ""
    # Prefer negatives and corrections
    ranked = sorted(
        outcomes,
        key=lambda o: (0 if o.correction else 1, o.score, -o.timestamp),
    )
    lines = ["PAST MISTAKES / LESSONS (adapt your answer):"]
    for o in ranked[:limit]:
        q = (o.query_summary or "")[:100]
        if o.score < 0:
            if o.correction:
                lines.append(
                    f"- When asked about '{q}', user wanted: {o.correction[:200]}"
                )
            else:
                lines.append(
                    f"- When asked about '{q}', the prior response was wrong "
                    f"(score {o.score}). Avoid repeating that approach."
                )
        elif o.score > 0:
            lines.append(
                f"- Similar ask '{q}' worked well previously — stay consistent."
            )
    return "\n".join(lines)


class OutcomeMemory:
    """SQLite-backed outcome tracker with keyword/tag retrieval."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else _default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending: PendingOutcome | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outcomes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        query_hash TEXT NOT NULL,
                        query_summary TEXT NOT NULL,
                        route TEXT NOT NULL,
                        response_summary TEXT NOT NULL,
                        score REAL NOT NULL,
                        tags_json TEXT NOT NULL,
                        correction TEXT NOT NULL DEFAULT '',
                        timestamp REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON outcomes(timestamp DESC)"
                )
                conn.commit()
            finally:
                conn.close()

    def set_pending(self, query: str, route: str, response: str) -> None:
        self._pending = PendingOutcome(
            query=query or "",
            route=route or "CHAT",
            response_summary=(response or "")[:200],
        )

    def consume_pending(self) -> PendingOutcome | None:
        pending = self._pending
        self._pending = None
        return pending

    def peek_pending(self) -> PendingOutcome | None:
        return self._pending

    def record(
        self,
        query: str,
        route: str,
        response: str,
        score: float,
        tags: list[str] | None = None,
        correction: str = "",
    ) -> None:
        q = (query or "").strip()
        if not q:
            return
        qhash = hashlib.sha256(q.encode("utf-8", errors="replace")).hexdigest()[:24]
        tags = tags or self._guess_tags(q)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO outcomes
                    (query_hash, query_summary, route, response_summary, score,
                     tags_json, correction, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        qhash,
                        q[:200],
                        route or "CHAT",
                        (response or "")[:200],
                        float(score),
                        json.dumps(tags),
                        (correction or "")[:400],
                        time.time(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        # Also store semantic note for TurboVec when score is informative
        if abs(score) >= 0.5:
            try:
                from knowledge.learner import remember_text

                remember_text(
                    f"Outcome score={score} route={route}\n"
                    f"Query: {q[:300]}\n"
                    f"Response: {(response or '')[:300]}\n"
                    f"Correction: {correction or 'n/a'}",
                    source="outcome_memory",
                    kind="outcome",
                )
            except Exception:
                pass

    def score_and_record_previous(self, current_input: str) -> float | None:
        """If a pending outcome exists, score it from the new user message and store."""
        pending = self.consume_pending()
        if not pending:
            return None
        score, correction = score_previous_outcome(current_input, pending.query)
        if score == 0.0 and not correction:
            # Still store neutral? Skip to avoid noise — but keep pending lost.
            # Re-set pending only if we want multi-turn; plan says score then clear.
            return 0.0
        self.record(
            pending.query,
            pending.route,
            pending.response_summary,
            score,
            correction=correction,
        )
        return score

    def get_relevant_lessons(self, query: str, limit: int = 3) -> list[Outcome]:
        q = (query or "").strip().lower()
        if not q:
            return []
        tokens = {t for t in re.findall(r"[a-z0-9_]+", q) if len(t) > 2}
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM outcomes ORDER BY timestamp DESC LIMIT 80"
                ).fetchall()
            finally:
                conn.close()

        scored: list[tuple[float, Outcome]] = []
        for r in rows:
            tags = []
            try:
                tags = json.loads(r["tags_json"] or "[]")
            except json.JSONDecodeError:
                tags = []
            summary = (r["query_summary"] or "").lower()
            overlap = sum(1 for t in tokens if t in summary)
            tag_hit = sum(1 for t in tags if str(t).lower() in tokens)
            # Prefer negatives
            score_bias = 2.0 if float(r["score"]) < 0 else (1.0 if float(r["score"]) > 0 else 0.2)
            if r["correction"]:
                score_bias += 1.5
            rank = (overlap * 2 + tag_hit) * score_bias
            if rank <= 0 and overlap == 0:
                continue
            scored.append(
                (
                    rank,
                    Outcome(
                        query_hash=r["query_hash"],
                        query_summary=r["query_summary"],
                        route=r["route"],
                        response_summary=r["response_summary"],
                        score=float(r["score"]),
                        tags=list(tags),
                        timestamp=float(r["timestamp"]),
                        correction=r["correction"] or "",
                    ),
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [o for _, o in scored[:limit]]

    def get_success_rate(self, route: str, window_days: int = 7) -> float:
        cutoff = time.time() - window_days * 86400
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT score FROM outcomes
                    WHERE route = ? AND timestamp >= ?
                    """,
                    (route, cutoff),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return 0.0
        wins = sum(1 for r in rows if float(r["score"]) > 0)
        return wins / len(rows)

    @staticmethod
    def _guess_tags(query: str) -> list[str]:
        low = query.lower()
        tags = []
        for t in (
            "coding", "bug", "fix", "nextjs", "python", "login", "auth",
            "web", "project", "desktop", "css", "jwt",
        ):
            if t in low:
                tags.append(t)
        return tags[:8]


_OUTCOME_SINGLETON: OutcomeMemory | None = None
_OUTCOME_LOCK = threading.Lock()


def get_outcome_memory() -> OutcomeMemory:
    global _OUTCOME_SINGLETON
    with _OUTCOME_LOCK:
        if _OUTCOME_SINGLETON is None:
            _OUTCOME_SINGLETON = OutcomeMemory()
        return _OUTCOME_SINGLETON


def outcome_learning_enabled() -> bool:
    try:
        from core.config import get_config

        return bool(getattr(get_config(), "outcome_learning", True))
    except Exception:
        import os

        raw = (os.environ.get("IMMORTILITY_OUTCOME_LEARNING") or "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def lessons_for_prompt(query: str) -> str:
    if not outcome_learning_enabled():
        return ""
    try:
        lessons = get_outcome_memory().get_relevant_lessons(query, limit=3)
        return format_lessons_block(lessons)
    except Exception as exc:
        logger.debug("lessons_for_prompt failed: %s", exc)
        return ""
