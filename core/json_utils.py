"""Shared helpers for parsing LLM JSON responses."""

from __future__ import annotations

import json
import re
from typing import Any


def strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)


def strip_llm_fences(text: str) -> str:
    """Remove <think> blocks and ```json / ``` fences; return inner text."""
    content = strip_think_blocks(text).strip()
    if "```json" in content:
        content = content.split("```json")[-1].split("```")[0].strip()
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1].split("```")[0].strip()
            # drop optional language tag on first line
            if "\n" in content:
                first, rest = content.split("\n", 1)
                if first.strip().isalpha() and len(first.strip()) < 12:
                    content = rest.strip()
    return content.strip()


def parse_llm_json(text: str) -> Any:
    """Parse JSON from an LLM reply (fences / think blocks allowed)."""
    return json.loads(strip_llm_fences(text))
