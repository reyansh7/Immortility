"""Normalize LLM patch args to FileTool signatures."""

from __future__ import annotations

from typing import Any


def normalize_patch_args(operation: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Map common LLM argument aliases to the keys FileTool expects.
    May also retarget the operation (e.g. edit_file + full content → write_file).
    """
    a = dict(args or {})
    op = operation

    # Shared aliases
    if "target" in a and "target_text" not in a:
        a["target_text"] = a.pop("target")
    if "old_text" in a and "target_text" not in a:
        a["target_text"] = a.pop("old_text")
    if "search" in a and "target_text" not in a:
        a["target_text"] = a.pop("search")
    if "find" in a and "target_text" not in a:
        a["target_text"] = a.pop("find")

    if "new_text" in a and "replacement_text" not in a:
        a["replacement_text"] = a.pop("new_text")
    if "replace" in a and "replacement_text" not in a and op == "edit_file":
        a["replacement_text"] = a.pop("replace")
    if "replacement" in a and "replacement_text" not in a and op == "edit_file":
        a["replacement_text"] = a.pop("replacement")

    if op == "edit_file":
        # LLM often uses "content" for the replacement block
        if "content" in a and "replacement_text" not in a:
            a["replacement_text"] = a.pop("content")
        # Full-file rewrite sent as edit_file
        if "replacement_text" in a and "target_text" not in a:
            op = "write_file"
            a["content"] = a.pop("replacement_text")
        elif "content" in a and "target_text" not in a:
            op = "write_file"

    elif op == "write_file":
        if "content" not in a:
            for key in ("replacement_text", "new_text", "body", "file_content"):
                if key in a:
                    a["content"] = a.pop(key)
                    break

    elif op == "create_file":
        if "content" not in a:
            for key in ("body", "file_content", "replacement_text", "new_text"):
                if key in a:
                    a["content"] = a.pop(key)
                    break

    elif op in ("insert_before", "insert_after", "append_file"):
        if "content" not in a:
            for key in ("replacement_text", "new_text", "insert", "text"):
                if key in a:
                    a["content"] = a.pop(key)
                    break

    elif op == "replace_lines":
        if "start_line" not in a and "start" in a:
            a["start_line"] = a.pop("start")
        if "end_line" not in a and "end" in a:
            a["end_line"] = a.pop("end")
        if "replacement" not in a:
            for key in ("content", "replacement_text", "new_text", "lines"):
                if key in a:
                    a["replacement"] = a.pop(key)
                    break

    elif op == "replace_regex":
        if "pattern" not in a and "regex" in a:
            a["pattern"] = a.pop("regex")
        if "replacement" not in a:
            for key in ("content", "replacement_text", "new_text", "replace"):
                if key in a:
                    a["replacement"] = a.pop(key)
                    break

    elif op == "delete_block":
        if "target_text" not in a:
            for key in ("content", "block", "text"):
                if key in a:
                    a["target_text"] = a.pop(key)
                    break

    elif op == "rename_symbol":
        if "old_name" not in a and "old_symbol" in a:
            a["old_name"] = a.pop("old_symbol")
        if "new_name" not in a and "new_symbol" in a:
            a["new_name"] = a.pop("new_symbol")

    elif op == "apply_patch":
        if "patch" not in a and "diff" in a:
            a["patch"] = a.pop("diff")
        if "patch" not in a and "content" in a:
            a["patch"] = a.pop("content")

    return op, a
