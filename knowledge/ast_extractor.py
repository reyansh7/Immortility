"""Tree-sitter AST extractor for symbols, imports, and call relationships."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass
class FileStructure:
    path: str
    language: str
    sha256: str
    symbols: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        exports = [
            f"{s['kind']}:{s['name']}"
            for s in self.symbols
            if s.get("kind") in ("function", "class", "method", "export", "component")
        ]
        deps = sorted(
            {
                d.get("source_module") or d.get("imported_name") or ""
                for d in self.dependencies
            }
            - {""}
        )
        purpose = _infer_purpose(Path(self.path).name, exports, deps)
        return {
            "purpose": purpose,
            "exports": exports[:40],
            "dependencies": deps[:40],
        }


def _infer_purpose(filename: str, exports: list[str], deps: list[str]) -> str:
    bits = [f"File `{filename}`"]
    if exports:
        bits.append(f"exports {', '.join(exports[:6])}")
    if deps:
        bits.append(f"depends on {', '.join(deps[:6])}")
    return "; ".join(bits) + "."


class ASTExtractor:
    """Parse a source file into structured graph nodes (tree-sitter preferred)."""

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._ready = False

    def _init(self) -> None:
        if self._ready:
            return
        self._ready = True
        try:
            import tree_sitter
            import tree_sitter_python as tspython
            import tree_sitter_javascript as tsjavascript
            import tree_sitter_typescript as tstypescript

            self._parsers["python"] = tree_sitter.Parser(
                tree_sitter.Language(tspython.language())
            )
            self._parsers["javascript"] = tree_sitter.Parser(
                tree_sitter.Language(tsjavascript.language())
            )
            self._parsers["typescript"] = tree_sitter.Parser(
                tree_sitter.Language(tstypescript.language_typescript())
            )
            self._parsers["tsx"] = tree_sitter.Parser(
                tree_sitter.Language(tstypescript.language_tsx())
            )
        except Exception as exc:
            logger.warning("ASTExtractor tree-sitter unavailable: %s", exc)

    def extract(self, filepath: str | Path) -> FileStructure | None:
        path = Path(filepath)
        if not path.is_file():
            return None
        lang = EXT_LANG.get(path.suffix.lower())
        if not lang:
            return None
        try:
            raw = path.read_bytes()
            source = raw.decode("utf-8", errors="ignore")
        except OSError:
            return None
        sha = hashlib.sha256(raw).hexdigest()
        self._init()
        if lang in self._parsers:
            if lang == "python":
                return self._extract_python(str(path.resolve()), source, sha)
            return self._extract_js_family(str(path.resolve()), source, sha, lang)
        return self._extract_regex(str(path.resolve()), source, sha, lang)

    # ── Python ──────────────────────────────────────────────────────

    def _extract_python(self, path: str, source: str, sha: str) -> FileStructure:
        parser = self._parsers["python"]
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
        symbols: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []

        def text(node: Any) -> str:
            return source[node.start_byte : node.end_byte]

        def walk(node: Any, current_fn: str = "") -> None:
            t = node.type
            if t == "import_statement":
                raw = text(node)
                for child in node.children:
                    if child.type == "dotted_name":
                        mod = text(child)
                        dependencies.append(
                            {"imported_name": mod, "source_module": mod, "raw": raw}
                        )
            elif t == "import_from_statement":
                raw = text(node)
                module = ""
                names: list[str] = []
                for child in node.children:
                    if child.type in ("dotted_name", "relative_import"):
                        if not module:
                            module = text(child)
                    elif child.type == "dotted_name" and module:
                        names.append(text(child))
                    elif child.type == "identifier":
                        names.append(text(child))
                if not names:
                    names = [module or raw]
                for n in names:
                    dependencies.append(
                        {
                            "imported_name": n,
                            "source_module": module or n,
                            "raw": raw,
                        }
                    )
            elif t == "function_definition":
                name = ""
                for child in node.children:
                    if child.type == "identifier":
                        name = text(child)
                        break
                if name:
                    symbols.append(
                        {
                            "name": name,
                            "kind": "function",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "parent_symbol": current_fn,
                        }
                    )
                    for child in node.children:
                        walk(child, current_fn=name)
                    return
            elif t == "class_definition":
                name = ""
                for child in node.children:
                    if child.type == "identifier":
                        name = text(child)
                        break
                if name:
                    symbols.append(
                        {
                            "name": name,
                            "kind": "class",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "parent_symbol": "",
                        }
                    )
                    for child in node.children:
                        walk(child, current_fn=name)
                    return
            elif t == "call" and current_fn:
                callee = ""
                # first child is usually function / attribute
                if node.child_count:
                    head = node.children[0]
                    if head.type == "identifier":
                        callee = text(head)
                    elif head.type == "attribute":
                        # take the attribute name (rightmost identifier)
                        ids = [text(c) for c in head.children if c.type == "identifier"]
                        callee = ids[-1] if ids else text(head)
                if callee and callee not in ("print", "len", "str", "int", "list", "dict"):
                    calls.append(
                        {
                            "caller": current_fn,
                            "callee": callee,
                            "line": node.start_point[0] + 1,
                        }
                    )
            for child in node.children:
                walk(child, current_fn=current_fn)

        walk(root)
        return FileStructure(
            path=path,
            language="python",
            sha256=sha,
            symbols=symbols,
            dependencies=_dedupe_deps(dependencies),
            calls=_dedupe_calls(calls),
        )

    # ── JS / TS ─────────────────────────────────────────────────────

    def _extract_js_family(
        self, path: str, source: str, sha: str, lang: str
    ) -> FileStructure:
        parser = self._parsers[lang]
        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
        symbols: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []

        def text(node: Any) -> str:
            return source[node.start_byte : node.end_byte]

        def walk(node: Any, current_fn: str = "") -> None:
            t = node.type
            if t in ("import_statement", "import_declaration"):
                raw = text(node)
                module = ""
                for child in node.children:
                    if child.type == "string":
                        module = text(child).strip("'\"")
                dependencies.append(
                    {
                        "imported_name": module or raw[:80],
                        "source_module": module,
                        "raw": raw,
                    }
                )
            elif t in ("function_declaration", "method_definition", "function"):
                name = ""
                for child in node.children:
                    if child.type in ("identifier", "property_identifier"):
                        name = text(child)
                        break
                if name:
                    kind = "method" if t == "method_definition" else "function"
                    symbols.append(
                        {
                            "name": name,
                            "kind": kind,
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "parent_symbol": current_fn,
                        }
                    )
                    for child in node.children:
                        walk(child, current_fn=name)
                    return
            elif t == "class_declaration":
                name = ""
                for child in node.children:
                    if child.type in ("identifier", "type_identifier"):
                        name = text(child)
                        break
                if name:
                    symbols.append(
                        {
                            "name": name,
                            "kind": "class",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "parent_symbol": "",
                        }
                    )
                    for child in node.children:
                        walk(child, current_fn=name)
                    return
            elif t == "lexical_declaration":
                # const Foo = (...) => / function
                raw = text(node)
                m = re.search(
                    r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function)",
                    raw,
                )
                if m:
                    name = m.group(1)
                    symbols.append(
                        {
                            "name": name,
                            "kind": "function",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                            "parent_symbol": current_fn,
                        }
                    )
                    for child in node.children:
                        walk(child, current_fn=name)
                    return
            elif t == "call_expression" and current_fn:
                callee = ""
                if node.child_count:
                    head = node.children[0]
                    if head.type == "identifier":
                        callee = text(head)
                    elif head.type == "member_expression":
                        ids = [
                            text(c)
                            for c in head.children
                            if c.type in ("identifier", "property_identifier")
                        ]
                        callee = ids[-1] if ids else ""
                if callee:
                    calls.append(
                        {
                            "caller": current_fn,
                            "callee": callee,
                            "line": node.start_point[0] + 1,
                        }
                    )
            for child in node.children:
                walk(child, current_fn=current_fn)

        walk(root)
        return FileStructure(
            path=path,
            language=lang,
            sha256=sha,
            symbols=symbols,
            dependencies=_dedupe_deps(dependencies),
            calls=_dedupe_calls(calls),
        )

    # ── Regex fallback ──────────────────────────────────────────────

    def _extract_regex(
        self, path: str, source: str, sha: str, lang: str
    ) -> FileStructure:
        symbols: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if lang == "python":
                if s.startswith("import ") or s.startswith("from "):
                    dependencies.append(
                        {"imported_name": s, "source_module": s.split()[1] if len(s.split()) > 1 else s, "raw": s}
                    )
                m = re.match(r"^(?:async\s+)?def\s+(\w+)", s)
                if m:
                    symbols.append(
                        {"name": m.group(1), "kind": "function", "start_line": i, "end_line": i, "parent_symbol": ""}
                    )
                m = re.match(r"^class\s+(\w+)", s)
                if m:
                    symbols.append(
                        {"name": m.group(1), "kind": "class", "start_line": i, "end_line": i, "parent_symbol": ""}
                    )
            else:
                if s.startswith("import ") or "require(" in s:
                    dependencies.append(
                        {"imported_name": s[:80], "source_module": s[:80], "raw": s}
                    )
                m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", s)
                if m:
                    symbols.append(
                        {"name": m.group(1), "kind": "function", "start_line": i, "end_line": i, "parent_symbol": ""}
                    )
                m = re.match(r"^(?:export\s+)?class\s+(\w+)", s)
                if m:
                    symbols.append(
                        {"name": m.group(1), "kind": "class", "start_line": i, "end_line": i, "parent_symbol": ""}
                    )
        # crude call edges: function bodies calling known names
        known = {s["name"] for s in symbols}
        current = ""
        for i, line in enumerate(lines, 1):
            m = re.match(r"^\s*(?:async\s+)?def\s+(\w+)|^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
            if m:
                current = m.group(1) or m.group(2) or ""
            if current:
                for name in known:
                    if name != current and re.search(rf"\b{re.escape(name)}\s*\(", line):
                        calls.append({"caller": current, "callee": name, "line": i})
        return FileStructure(
            path=path,
            language=lang,
            sha256=sha,
            symbols=symbols,
            dependencies=_dedupe_deps(dependencies),
            calls=_dedupe_calls(calls),
        )


def _dedupe_deps(deps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in deps:
        key = f"{d.get('source_module')}|{d.get('imported_name')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _dedupe_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in calls:
        key = f"{c.get('caller')}|{c.get('callee')}|{c.get('line')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
