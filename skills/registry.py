"""Reusable coding skills and rules. Not a second orchestration framework.

Inspired by ECC (on-demand skills, durable rules, deterministic hooks) but
not vendored from it: no ``.claude/``, no ECC skill markdown, no hooks.json.
Skills are prompt/rule snippets matched to a request. Planner, Coder, and
Reviewer stay thin: they consume ``prompt_block()``, they do not embed policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.repo_paths import get_repo_root

_RULES_DIR = Path("prompts") / "rules"


@dataclass(frozen=True)
class Skill:
    name: str
    keywords: tuple[str, ...]
    rule_file: str
    summary: str
    hooks: tuple[str, ...] = ()
    always: bool = False


_BUILTIN: tuple[Skill, ...] = (
    Skill(
        name="python_edit",
        keywords=("python", ".py", "pytest", "refactor", "implement", "fix", "bug"),
        rule_file="python.md",
        summary="Smallest Python change; syntax-check touched files.",
        hooks=("before_plan", "before_edit"),
    ),
    Skill(
        name="git_safety",
        keywords=("git", "commit", "push", "reset", "branch"),
        rule_file="git_safety.md",
        summary="No silent destructive git. Confirmation gates stay in pending_action.",
        hooks=("before_edit", "before_execute", "before_finalize"),
        always=True,
    ),
    Skill(
        name="testing",
        keywords=("test", "pytest", "verify", "regression"),
        rule_file="testing.md",
        summary="Explicit success criteria; targeted checks only.",
        hooks=("before_plan", "after_execute", "before_finalize"),
        always=True,
    ),
    Skill(
        name="inspect_first",
        keywords=("implement", "add", "create", "fix", "inspect", "read"),
        rule_file="inspect_first.md",
        summary="Inspect named files before editing. Do not re-inspect on every retry.",
        hooks=("before_plan", "before_inspect"),
    ),
    Skill(
        name="plan_first",
        keywords=("implement", "feature", "multi-file", "refactor"),
        rule_file="plan_first.md",
        summary="Plan before editing. Heuristic planner is enough for simple tasks.",
        hooks=("before_plan",),
    ),
    Skill(
        name="smallest_change",
        keywords=(),
        rule_file="smallest_change.md",
        summary="Change only what the request requires.",
        hooks=("before_edit",),
        always=True,
    ),
    Skill(
        name="safety",
        keywords=("secret", "token", "password", "force", "reset"),
        rule_file="safety.md",
        summary="Do not print secrets or weaken git/confirmation gates.",
        hooks=("before_edit", "before_execute", "before_finalize"),
        always=True,
    ),
)


def _rules_root() -> Path:
    return get_repo_root() / _RULES_DIR


def load_rule(name: str) -> str:
    path = _rules_root() / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class SkillRegistry:
    """Process-local registry. Match is keyword-only — no LLM."""

    def __init__(self, skills: tuple[Skill, ...] | None = None) -> None:
        self._skills = list(skills or _BUILTIN)

    def register(self, skill: Skill) -> None:
        self._skills = [s for s in self._skills if s.name != skill.name]
        self._skills.append(skill)

    def match(self, request: str) -> list[Skill]:
        text = (request or "").lower()
        found: list[Skill] = []
        for skill in self._skills:
            if skill.always or any(k.lower() in text for k in skill.keywords):
                found.append(skill)
        return found

    def prompt_block(self, request: str, *, limit: int = 2400) -> str:
        chunks: list[str] = []
        used = 0
        for skill in self.match(request):
            body = load_rule(skill.rule_file)
            piece = f"### Skill: {skill.name}\n{skill.summary}"
            if body:
                piece = f"{piece}\n{body}"
            if used + len(piece) > limit:
                break
            chunks.append(piece)
            used += len(piece)
        return "\n\n".join(chunks).strip()


_current: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _current
    if _current is None:
        _current = SkillRegistry()
    return _current


def reset_skill_registry() -> None:
    global _current
    _current = None
