"""Project-wide symbol and reference search."""

from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".md"}


class SymbolFinder:
    """Find symbol definitions and references across a project directory."""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()

    def _iter_source_files(self) -> list[Path]:
        files: list[Path] = []
        skip_dirs = {
            ".git", "venv", "node_modules", "__pycache__",
            ".venv", "vector_db", "logs", ".pytest_cache",
        }
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
        return files

    def find_symbol(self, symbol: str) -> list[dict]:
        """Locate symbol definitions."""
        results: list[dict] = []
        patterns = [
            (rf"^\s*class\s+{re.escape(symbol)}\b", "class"),
            (rf"^\s*def\s+{re.escape(symbol)}\b", "function"),
            (rf"^\s*async\s+def\s+{re.escape(symbol)}\b", "function"),
            (rf"(?:const|let|var|function)\s+{re.escape(symbol)}\b", "symbol"),
            (rf"^\s*{re.escape(symbol)}\s*=\s*", "variable"),
        ]

        for filepath in self._iter_source_files():
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(filepath.relative_to(self.root))
            for line_no, line in enumerate(content.splitlines(), 1):
                for pattern, kind in patterns:
                    if re.search(pattern, line):
                        results.append({
                            "file": rel,
                            "line": line_no,
                            "kind": kind,
                            "text": line.strip(),
                        })
                        break
        return results

    def find_references(self, symbol: str) -> list[dict]:
        """Locate all usages of a symbol."""
        results: list[dict] = []
        word_pattern = re.compile(rf"\b{re.escape(symbol)}\b")

        for filepath in self._iter_source_files():
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(filepath.relative_to(self.root))
            for line_no, line in enumerate(content.splitlines(), 1):
                if word_pattern.search(line):
                    kind = "definition" if self._is_definition_line(line, symbol) else "reference"
                    results.append({
                        "file": rel,
                        "line": line_no,
                        "kind": kind,
                        "text": line.strip(),
                    })
        return results

    @staticmethod
    def _is_definition_line(line: str, symbol: str) -> bool:
        stripped = line.strip()
        return bool(
            re.match(rf"(class|def|async def)\s+{re.escape(symbol)}\b", stripped)
            or re.match(rf"(const|let|var|function)\s+{re.escape(symbol)}\b", stripped)
        )
