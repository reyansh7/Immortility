"""Shared helpers for parsing LLM JSON responses."""

from __future__ import annotations

import json
import re
from typing import Any


def strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)


_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)```",
    re.DOTALL,
)


def strip_llm_fences(text: str) -> str:
    """Remove <think> blocks and fenced code; prefer the last JSON-looking fence."""
    content = strip_think_blocks(text).strip()
    matches = list(_FENCE_RE.finditer(content))
    if matches:
        # Prefer last fence that looks like JSON object/array
        for m in reversed(matches):
            inner = m.group(1).strip()
            if inner.startswith("{") or inner.startswith("["):
                return inner
        return matches[-1].group(1).strip()
    # Fallback: bare ``` without language
    if "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            chunk = parts[1].strip()
            if "\n" in chunk:
                first, rest = chunk.split("\n", 1)
                if first.strip().isalpha() and len(first.strip()) < 12:
                    chunk = rest.strip()
            return chunk
    return content.strip()


def parse_llm_json(text: str) -> Any:
    """Parse JSON from an LLM reply (fences / think blocks allowed)."""
    return json.loads(strip_llm_fences(text))
