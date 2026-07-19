import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class PatchValidator:
    """Validates patches before they are applied."""

    @staticmethod
    def validate_search_replace(file_content: str, target_text: str) -> tuple[bool, str]:
        if not target_text:
            return False, "Target text is empty."
        if target_text not in file_content:
            return False, "Target text not found in file content."
        return True, "OK"

    @staticmethod
    def validate_python_syntax(content: str) -> tuple[bool, str]:
        try:
            ast.parse(content)
            return True, "OK"
        except SyntaxError as e:
            return False, f"SyntaxError: {e}"
        except Exception as e:
            return False, f"Error parsing Python: {e}"

    @staticmethod
    def validate_brace_balance(content: str) -> tuple[bool, str]:
        """Count (), [], {} without string parsing — safe for TSX/JSX."""
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[str] = []
        for c in content:
            if c in pairs:
                stack.append(pairs[c])
            elif c in pairs.values():
                if not stack or stack[-1] != c:
                    return False, f"Unbalanced bracket/brace found: expected {stack[-1] if stack else 'nothing'} but got {c}"
                stack.pop()
        if stack:
            return False, f"Unclosed brackets/braces remaining: {stack}"
        return True, "OK"

    @staticmethod
    def validate_js_syntax(content: str) -> tuple[bool, str]:
        """Bracket balance for JS/TS with string-aware parsing."""
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[str] = []
        in_string = False
        quote = ""
        i = 0
        while i < len(content):
            c = content[i]
            if in_string:
                if c == "\\" and i + 1 < len(content):
                    i += 2
                    continue
                if c == quote:
                    in_string = False
                i += 1
                continue
            if c in ('"', "'", "`"):
                # Apostrophe in contractions (Don't, it's) — not a string delimiter
                if c == "'" and 0 < i < len(content) - 1:
                    if content[i - 1].isalnum() and content[i + 1].isalnum():
                        i += 1
                        continue
                in_string = True
                quote = c
                i += 1
                continue
            if c in pairs:
                stack.append(pairs[c])
            elif c in pairs.values():
                if not stack or stack[-1] != c:
                    return False, f"Unbalanced bracket/brace found: expected {stack[-1] if stack else 'nothing'} but got {c}"
                stack.pop()
            i += 1
        if stack:
            return False, f"Unclosed brackets/braces remaining: {stack}"
        if in_string:
            return False, f"Unclosed string literal starting with {quote}"
        return True, "OK"

    @staticmethod
    def validate_syntax(content: str, file_path: str = "") -> tuple[bool, str]:
        if file_path.endswith(".py"):
            return PatchValidator.validate_python_syntax(content)
        if file_path.endswith((".tsx", ".jsx")):
            return PatchValidator.validate_brace_balance(content)
        if file_path.endswith((".ts", ".js")):
            return PatchValidator.validate_js_syntax(content)
        return True, "OK"

    @staticmethod
    def validate_patch(file_path: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"valid": False, "message": f"File not found: {file_path}"}
        content = path.read_text(encoding="utf-8")
        valid, msg = PatchValidator.validate_syntax(content, file_path)
        if not valid:
            return {"valid": False, "message": f"Syntax validation failed: {msg}"}
        return {"valid": True, "message": "OK", "lines": content.count("\\n") + 1}
