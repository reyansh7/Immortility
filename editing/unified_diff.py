"""Apply unified diff patches to file content."""

from __future__ import annotations

import re


def apply_unified_diff(original: str, patch: str) -> str | None:
    """
    Apply a unified diff to original file content.
    Returns new content or None if the patch could not be applied.
    """
    orig_lines = original.splitlines(keepends=True)
    if not orig_lines and original:
        orig_lines = [original if original.endswith("\n") else original + "\n"]

    patch_lines = patch.splitlines(keepends=True)
    result: list[str] = []
    orig_idx = 0
    i = 0
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    while i < len(patch_lines):
        line = patch_lines[i]
        if not line.startswith("@@"):
            i += 1
            continue

        m = hunk_re.match(line.strip())
        if not m:
            i += 1
            continue

        old_start = int(m.group(1)) - 1
        while orig_idx < old_start and orig_idx < len(orig_lines):
            result.append(orig_lines[orig_idx])
            orig_idx += 1

        i += 1
        while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
            pl = patch_lines[i]
            if pl.startswith("+"):
                result.append(pl[1:] if len(pl) > 1 else "\n")
            elif pl.startswith("-"):
                if orig_idx >= len(orig_lines):
                    return None
                orig_idx += 1
            elif pl.startswith(" "):
                if orig_idx >= len(orig_lines):
                    return None
                result.append(orig_lines[orig_idx])
                orig_idx += 1
            elif pl.startswith("\\"):
                pass
            else:
                return None
            i += 1

    while orig_idx < len(orig_lines):
        result.append(orig_lines[orig_idx])
        orig_idx += 1

    return "".join(result)
