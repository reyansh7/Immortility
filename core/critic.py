"""Critic / verification gate before final answers (reduce hallucinations)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class CritiqueResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    needs_web: bool = False
    needs_tools: bool = False
    revised_hint: str = ""


def critic_enabled() -> bool:
    try:
        from core.config import get_config

        return bool(getattr(get_config(), "critic_enabled", True))
    except Exception:
        raw = (os.environ.get("IMMORTILITY_CRITIC") or "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}


_SPECIFIC_CLAIM = re.compile(
    r"\b(exactly|precisely|always|never|guaranteed|"
    r"\d{4}|\$\d+|version\s+\d+|released\s+on|as of today)\b",
    re.I,
)
_UNCERTAIN = re.compile(
    r"\b(i (do not|don't|cannot|can't) know|could not find|no (reliable )?sources|"
    r"unable to verify|not sure)\b",
    re.I,
)


def critique_answer(
    query: str,
    draft: str,
    *,
    sources: list[str] | None = None,
    mode: str = "LOCAL",
) -> CritiqueResult:
    """Lightweight deterministic critic (no LLM required)."""
    if not critic_enabled():
        return CritiqueResult(ok=True)

    text = (draft or "").strip()
    sources = [s for s in (sources or []) if s]
    issues: list[str] = []
    needs_web = False
    needs_tools = False

    if not text:
        return CritiqueResult(
            ok=False,
            issues=["empty answer"],
            needs_web=(mode in {"WEB", "MEMORY"}),
            revised_hint="Provide a short answer or admit uncertainty.",
        )

    if mode == "WEB":
        if not sources:
            if _SPECIFIC_CLAIM.search(text) and not _UNCERTAIN.search(text):
                issues.append("web mode answer has specific claims but no sources")
                needs_web = True
        else:
            # Prefer citations present
            if not any(s in text for s in sources) and "http" not in text.lower():
                issues.append("web answer missing source URLs")
                # Not fatal if sources were used upstream — warn only
                if len(text) > 80 and _SPECIFIC_CLAIM.search(text):
                    needs_web = False  # already researched; ask for citation append
                    issues.append("append citations")

    if mode in {"WEB", "MEMORY"} and _SPECIFIC_CLAIM.search(text) and not sources:
        if not _UNCERTAIN.search(text):
            issues.append("unsupported specific claims without evidence")
            needs_web = True

    # Task verbs without completion language for TOOLS summaries
    if mode == "TOOLS":
        if re.search(r"\b(i will|going to|should)\b", text, re.I) and not re.search(
            r"\b(done|completed|changed|created|fixed|verified)\b", text, re.I
        ):
            issues.append("tools answer sounds incomplete")
            needs_tools = True

    ok = not needs_web and not needs_tools and not any(
        i.startswith("unsupported") or i.startswith("empty") for i in issues
    )
    # Soft citation warning alone is ok
    if issues == ["append citations"] or (
        issues and all(i == "append citations" for i in issues)
    ):
        ok = True
        hint = "Append a Sources list with the URLs used."
    else:
        hint = ""
        if needs_web:
            hint = "Search the web again and cite URLs; do not invent current facts."
        elif needs_tools:
            hint = "Finish the tool actions or report what blocked completion."
        elif issues:
            hint = issues[0]

    return CritiqueResult(
        ok=ok,
        issues=issues,
        needs_web=needs_web,
        needs_tools=needs_tools,
        revised_hint=hint,
    )


def apply_critic_or_retry(
    query: str,
    draft: str,
    *,
    sources: list[str] | None = None,
    mode: str = "LOCAL",
    retry_fn=None,
) -> str:
    """Run critic once; optional single retry via retry_fn() -> (new_draft, sources)."""
    critique = critique_answer(query, draft, sources=sources, mode=mode)
    if critique.ok:
        if "append citations" in critique.issues and sources:
            cites = "\n".join(f"- {u}" for u in sources[:8])
            if "Sources:" not in (draft or ""):
                return f"{draft.rstrip()}\n\nSources:\n{cites}"
        return draft

    if critique.needs_web and retry_fn is not None:
        try:
            new_draft, new_sources = retry_fn()
            second = critique_answer(
                query, new_draft, sources=new_sources or sources, mode=mode
            )
            if second.ok or (new_draft or "").strip():
                text = new_draft or draft
                if new_sources and "http" not in text.lower():
                    text += "\n\nSources:\n" + "\n".join(f"- {u}" for u in new_sources[:8])
                return text
        except Exception:
            pass

    # Fail closed with uncertainty rather than hallucinate
    if critique.needs_web:
        return (
            (draft[:500] + "\n\n" if draft else "")
            + "I could not verify this with reliable sources, so I am not inventing details. "
            + (critique.revised_hint or "")
        ).strip()
    return draft
