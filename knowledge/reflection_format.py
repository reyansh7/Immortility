"""Format KG reflections for planning prompts (no heavy imports)."""

from __future__ import annotations

from typing import Any


def format_reflections_block(
    reflections: list[dict[str, Any]], goal: str = "", limit: int = 5
) -> str:
    """Filter overlapping reflections and format for planning prompts."""
    if not reflections:
        return ""
    goal_terms = {t for t in (goal or "").lower().split() if len(t) >= 3}

    def _overlap(task: str) -> int:
        low = (task or "").lower()
        return sum(1 for t in goal_terms if t in low)

    ranked = sorted(
        reflections,
        key=lambda r: _overlap(str(r.get("task") or "")),
        reverse=True,
    )
    picked = [r for r in ranked if _overlap(str(r.get("task") or "")) > 0][:limit]
    if not picked:
        picked = ranked[: min(2, limit)]
    lines = ["PAST REFLECTIONS ON SIMILAR TASKS:"]
    for r in picked:
        task = str(r.get("task") or "")[:120]
        broke = str(r.get("what_broke") or "").strip().replace("\n", " ")[:200]
        fixed = str(r.get("what_fixed_it") or "").strip().replace("\n", " ")[:200]
        lines.append(f"- Task: {task}")
        if broke:
            lines.append(f"  FAILED: {broke}")
        if fixed:
            lines.append(f"  FIXED BY: {fixed}")
    return "\n".join(lines)
