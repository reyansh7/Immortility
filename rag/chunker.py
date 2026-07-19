"""Smart code chunking using tree-sitter AST parsing.

Splits source files into semantically meaningful chunks:
  - Python:  functions, classes, methods, docstrings
  - JavaScript / TypeScript:  functions, components, classes, exports
  - Markdown:  headings / sections
  - Generic (JSON, YAML, TOML, .env):  logical blocks

Every chunk carries full provenance metadata so the retriever can
rank and display results meaningfully.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Data model ──────────────────────────────────────────────────────


@dataclass
class CodeChunk:
    """A semantically meaningful chunk of source code or documentation."""

    content: str
    filename: str
    language: str
    chunk_type: str  # function | class | method | section | module | export | component
    project: str = ""
    class_name: str = ""
    function_name: str = ""
    imports: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        """Unique identifier for this chunk within a project."""
        parts = [self.project, self.filename, self.chunk_type]
        if self.class_name:
            parts.append(self.class_name)
        if self.function_name:
            parts.append(self.function_name)
        parts.append(str(self.start_line))
        return ":".join(filter(None, parts))

    def to_search_text(self) -> str:
        """Searchable text representation including metadata context."""
        header_parts: list[str] = [f"File: {self.filename}"]
        if self.class_name:
            header_parts.append(f"Class: {self.class_name}")
        if self.function_name:
            header_parts.append(f"Function: {self.function_name}")
        header_parts.append(self.content)
        return "\n".join(header_parts)


# ── Extension → language mapping ────────────────────────────────────

EXTENSION_MAP: dict[str, str] = {
    # AST-parsed languages
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    # Structured text
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "markdown",
    # Data / config
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "generic",
    ".ini": "generic",
    ".cfg": "generic",
    ".conf": "generic",
    ".env": "env",
    ".prisma": "generic",
    ".graphql": "generic",
    ".gql": "generic",
    ".proto": "generic",
    # Web
    ".html": "generic",
    ".htm": "generic",
    ".css": "generic",
    ".scss": "generic",
    ".sass": "generic",
    ".less": "generic",
    ".svg": "generic",
    # Systems / compiled (chunked generically)
    ".java": "generic",
    ".go": "generic",
    ".rs": "generic",
    ".c": "generic",
    ".cpp": "generic",
    ".h": "generic",
    ".hpp": "generic",
    ".cs": "generic",
    ".rb": "generic",
    ".php": "generic",
    ".swift": "generic",
    ".kt": "generic",
    ".kts": "generic",
    ".dart": "generic",
    ".r": "generic",
    ".lua": "generic",
    ".ex": "generic",
    ".exs": "generic",
    ".erl": "generic",
    # Scripting
    ".sh": "generic",
    ".bash": "generic",
    ".bat": "generic",
    ".ps1": "generic",
    ".sql": "generic",
    # Docs
    ".txt": "generic",
}

# Target chunk size: 500-800 tokens ≈ 2000-3200 chars
# Overlap: 100-150 tokens ≈ 400-600 chars (15-20% of chunk)
GENERIC_CHUNK_MAX_CHARS = 3200   # ~800 tokens
GENERIC_CHUNK_OVERLAP_CHARS = 500  # ~125 tokens

# Code-aware separators, ordered from strongest to weakest boundary
CODE_SEPARATORS = [
    "\nclass ",
    "\ndef ",
    "\nasync def ",
    "\nfunction ",
    "\nexport ",
    "\n\n",
    "\n",
]


# ── Chunker ─────────────────────────────────────────────────────────


class Chunker:
    """Splits source files into semantically meaningful chunks.

    Uses tree-sitter when available for Python, JavaScript and
    TypeScript.  Falls back to regex-based splitting if tree-sitter
    cannot be loaded.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._initialized: bool = False

    # ── Parser bootstrap ────────────────────────────────────────────

    def _init_parsers(self) -> None:
        """Lazy-initialise tree-sitter parsers for all supported languages."""
        if self._initialized:
            return
        self._initialized = True
        try:
            import tree_sitter
            # pyrefly: ignore [missing-import]
            import tree_sitter_python as tspython
            # pyrefly: ignore [missing-import]
            import tree_sitter_javascript as tsjavascript
            # pyrefly: ignore [missing-import]
            import tree_sitter_typescript as tstypescript

            self._parsers["python"] = tree_sitter.Parser(tree_sitter.Language(tspython.language()))
            self._parsers["javascript"] = tree_sitter.Parser(
                tree_sitter.Language(tsjavascript.language())
            )
            self._parsers["typescript"] = tree_sitter.Parser(
                tree_sitter.Language(tstypescript.language_typescript())
            )
            self._parsers["tsx"] = tree_sitter.Parser(
                tree_sitter.Language(tstypescript.language_tsx())
            )
            logger.info(
                "Tree-sitter parsers ready: %s", list(self._parsers.keys())
            )
        except Exception as exc:
            logger.warning(
                "Tree-sitter unavailable, using regex fallback: %s", exc
            )

    # ── Public dispatch ─────────────────────────────────────────────

    def chunk_file(
        self, filepath: str | Path, project: str = ""
    ) -> list[CodeChunk]:
        """Split a file into chunks, auto-detecting its language."""
        filepath = Path(filepath)
        if not filepath.exists():
            logger.warning("File not found: %s", filepath)
            return []

        name_lower = filepath.name.lower()
        if name_lower == ".env.example":
            language = "env"
        else:
            language = EXTENSION_MAP.get(filepath.suffix.lower(), "")
        if not language:
            return []

        try:
            source = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.error("Cannot read %s: %s", filepath, exc)
            return []

        if not source.strip():
            return []

        dispatch: dict[str, Callable[..., list[CodeChunk]]] = {
            "python": self._chunk_python,
            "javascript": self._chunk_js,
            "typescript": self._chunk_ts,
            "tsx": self._chunk_tsx,
            "markdown": self._chunk_markdown,
            "json": self._chunk_generic,
            "yaml": self._chunk_generic,
            "toml": self._chunk_generic,
            "env": self._chunk_generic,
        }

        fn = dispatch.get(language, self._chunk_generic)
        chunks = fn(source, str(filepath), language, project)

        if not chunks:
            chunks = [
                CodeChunk(
                    content=source[:8000],
                    filename=str(filepath),
                    language=language,
                    chunk_type="module",
                    project=project,
                    start_line=1,
                    end_line=source.count("\n") + 1,
                )
            ]
        return chunks

    # ── Python ──────────────────────────────────────────────────────

    def _chunk_python(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        self._init_parsers()
        parser = self._parsers.get("python")
        if parser is None:
            return self._chunk_python_regex(source, filename, lang, project)

        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node
        chunks: list[CodeChunk] = []

        # Collect module-level imports
        imports: list[str] = []
        for child in root.children:
            if child.type in ("import_statement", "import_from_statement"):
                imports.append(source[child.start_byte : child.end_byte])

        for child in root.children:
            if child.type == "function_definition":
                chunks.append(
                    self._py_function(child, source, filename, project, imports)
                )
            elif child.type == "class_definition":
                chunks.extend(
                    self._py_class(child, source, filename, project, imports)
                )
            elif child.type == "decorated_definition":
                for sub in child.children:
                    if sub.type == "function_definition":
                        chunks.append(
                            self._py_function(
                                child, source, filename, project, imports
                            )
                        )
                        break
                    if sub.type == "class_definition":
                        chunks.extend(
                            self._py_class(
                                child, source, filename, project, imports
                            )
                        )
                        break
        return chunks

    def _py_function(
        self, node: Any, source: str, filename: str, project: str,
        imports: list[str],
    ) -> CodeChunk:
        text = source[node.start_byte : node.end_byte]
        name = self._find_child_text(node, "identifier", source)
        # For decorated_definition, look deeper
        if not name and node.type == "decorated_definition":
            for sub in node.children:
                if sub.type == "function_definition":
                    name = self._find_child_text(sub, "identifier", source)
                    break
        return CodeChunk(
            content=text,
            filename=filename,
            language="python",
            chunk_type="function",
            project=project,
            function_name=name,
            imports=imports[:],
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )

    def _py_class(
        self, node: Any, source: str, filename: str, project: str,
        imports: list[str],
    ) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []
        text = source[node.start_byte : node.end_byte]

        # Resolve actual class node (may be inside decorated_definition)
        cls_node = node
        if node.type == "decorated_definition":
            for sub in node.children:
                if sub.type == "class_definition":
                    cls_node = sub
                    break

        class_name = self._find_child_text(cls_node, "identifier", source)

        # Full class chunk
        chunks.append(
            CodeChunk(
                content=text,
                filename=filename,
                language="python",
                chunk_type="class",
                project=project,
                class_name=class_name,
                imports=imports[:],
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )

        # Individual method chunks
        body = self._find_child(cls_node, "block")
        if body is None:
            return chunks

        for child in body.children:
            method_node = child
            if child.type == "decorated_definition":
                for sub in child.children:
                    if sub.type == "function_definition":
                        method_node = child
                        break

            if method_node.type not in (
                "function_definition",
                "decorated_definition",
            ):
                continue

            fn_node = method_node
            if method_node.type == "decorated_definition":
                for sub in method_node.children:
                    if sub.type == "function_definition":
                        fn_node = sub
                        break

            method_name = self._find_child_text(fn_node, "identifier", source)
            chunks.append(
                CodeChunk(
                    content=source[method_node.start_byte : method_node.end_byte],
                    filename=filename,
                    language="python",
                    chunk_type="method",
                    project=project,
                    class_name=class_name,
                    function_name=method_name,
                    imports=imports[:],
                    start_line=method_node.start_point[0] + 1,
                    end_line=method_node.end_point[0] + 1,
                )
            )
        return chunks

    def _chunk_python_regex(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        """Fallback Python chunking using regex."""
        chunks: list[CodeChunk] = []
        imports = [
            l.strip()
            for l in source.splitlines()
            if l.strip().startswith(("import ", "from "))
        ]

        pattern = re.compile(r"^(class |def |async def )", re.MULTILINE)
        matches = list(pattern.finditer(source))
        if not matches:
            return []

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
            text = source[start:end].rstrip()
            start_line = source[:start].count("\n") + 1
            end_line = start_line + text.count("\n")
            first = text.split("\n")[0].strip()

            if first.startswith("class "):
                ctype, cname, fname = "class", "", ""
                m = re.match(r"class\s+(\w+)", first)
                if m:
                    cname = m.group(1)
            else:
                ctype, cname, fname = "function", "", ""
                m = re.match(r"(?:async\s+)?def\s+(\w+)", first)
                if m:
                    fname = m.group(1)

            chunks.append(
                CodeChunk(
                    content=text,
                    filename=filename,
                    language=lang,
                    chunk_type=ctype,
                    project=project,
                    class_name=cname,
                    function_name=fname,
                    imports=imports[:],
                    start_line=start_line,
                    end_line=end_line,
                )
            )
        return chunks

    # ── JavaScript / TypeScript ─────────────────────────────────────

    def _chunk_js(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        self._init_parsers()
        parser = self._parsers.get("javascript")
        if parser is None:
            return self._chunk_js_regex(source, filename, lang, project)
        tree = parser.parse(source.encode("utf-8"))
        return self._extract_js_chunks(tree.root_node, source, filename, lang, project)

    def _chunk_ts(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        self._init_parsers()
        parser = self._parsers.get("typescript")
        if parser is None:
            return self._chunk_js_regex(source, filename, lang, project)
        tree = parser.parse(source.encode("utf-8"))
        return self._extract_js_chunks(tree.root_node, source, filename, lang, project)

    def _chunk_tsx(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        self._init_parsers()
        parser = self._parsers.get("tsx")
        if parser is None:
            return self._chunk_js_regex(source, filename, lang, project)
        tree = parser.parse(source.encode("utf-8"))
        return self._extract_js_chunks(tree.root_node, source, filename, lang, project)

    def _extract_js_chunks(
        self, root: Any, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []
        imports: list[str] = []

        for child in root.children:
            text = source[child.start_byte : child.end_byte]
            sl = child.start_point[0] + 1
            el = child.end_point[0] + 1

            if child.type in ("import_statement", "import_declaration"):
                imports.append(text)
                continue

            if child.type == "function_declaration":
                name = self._find_child_text(child, "identifier", source)
                chunks.append(
                    CodeChunk(
                        content=text, filename=filename, language=lang,
                        chunk_type="function", project=project,
                        function_name=name, imports=imports[:],
                        start_line=sl, end_line=el,
                    )
                )

            elif child.type == "class_declaration":
                name = self._find_child_text(child, "identifier", source)
                chunks.append(
                    CodeChunk(
                        content=text, filename=filename, language=lang,
                        chunk_type="class", project=project,
                        class_name=name, imports=imports[:],
                        start_line=sl, end_line=el,
                    )
                )

            elif child.type == "export_statement":
                ct, cn, fn = self._classify_export(child, source)
                chunks.append(
                    CodeChunk(
                        content=text, filename=filename, language=lang,
                        chunk_type=ct, project=project,
                        class_name=cn, function_name=fn,
                        imports=imports[:], start_line=sl, end_line=el,
                    )
                )

            elif child.type in ("lexical_declaration", "variable_declaration"):
                name, is_fn = self._detect_arrow_fn(child, source)
                if is_fn and name:
                    ct = "component" if name[0].isupper() else "function"
                    chunks.append(
                        CodeChunk(
                            content=text, filename=filename, language=lang,
                            chunk_type=ct, project=project,
                            function_name=name, imports=imports[:],
                            start_line=sl, end_line=el,
                        )
                    )
        return chunks

    def _classify_export(
        self, node: Any, source: str
    ) -> tuple[str, str, str]:
        """Classify an export_statement and return (chunk_type, class_name, func_name)."""
        for sub in node.children:
            if sub.type == "function_declaration":
                name = self._find_child_text(sub, "identifier", source)
                return "function", "", name
            if sub.type == "class_declaration":
                name = self._find_child_text(sub, "identifier", source)
                return "class", name, ""
            if sub.type in ("lexical_declaration", "variable_declaration"):
                name, is_fn = self._detect_arrow_fn(sub, source)
                if is_fn and name:
                    ct = "component" if name[0].isupper() else "function"
                    return ct, "", name
        return "export", "", ""

    def _detect_arrow_fn(
        self, node: Any, source: str
    ) -> tuple[str, bool]:
        """Check if a declaration contains an arrow function. Return (name, is_fn)."""
        name = ""
        is_fn = False
        for decl in node.children:
            if decl.type == "variable_declarator":
                for sub in decl.children:
                    if sub.type == "identifier" and not name:
                        name = source[sub.start_byte : sub.end_byte]
                    elif sub.type in ("arrow_function", "function"):
                        is_fn = True
        return name, is_fn

    def _chunk_js_regex(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        """Fallback JS / TS chunking using regex."""
        chunks: list[CodeChunk] = []
        imports = [
            l.strip()
            for l in source.splitlines()
            if l.strip().startswith(("import ", "require("))
        ]

        patterns: list[tuple[str, str]] = [
            (r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", "function"),
            (r"^(?:export\s+)?class\s+(\w+)", "class"),
            (r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:\([^)]*\)\s*=>|[^=]\s*=>)", "function"),
        ]

        for pattern, ctype in patterns:
            for match in re.finditer(pattern, source, re.MULTILINE):
                name = match.group(1)
                start = match.start()
                start_line = source[:start].count("\n") + 1
                end = self._find_block_end(source, start)
                text = source[start:end]
                end_line = start_line + text.count("\n")

                chunks.append(
                    CodeChunk(
                        content=text, filename=filename, language=lang,
                        chunk_type=ctype, project=project,
                        function_name=name if ctype != "class" else "",
                        class_name=name if ctype == "class" else "",
                        imports=imports[:],
                        start_line=start_line, end_line=end_line,
                    )
                )
        return chunks

    # ── Markdown ────────────────────────────────────────────────────

    def _chunk_markdown(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        """Split Markdown by headings into section chunks."""
        sections = re.split(r"^(#{1,6}\s+.+)$", source, flags=re.MULTILINE)

        if len(sections) <= 1:
            return [
                CodeChunk(
                    content=source[:8000], filename=filename,
                    language="markdown", chunk_type="section",
                    project=project, start_line=1,
                    end_line=source.count("\n") + 1,
                )
            ]

        chunks: list[CodeChunk] = []
        heading = ""
        body = sections[0]
        offset = 1

        for i in range(1, len(sections), 2):
            if heading and body.strip():
                combined = f"{heading}\n{body}".strip()
                end_line = offset + combined.count("\n")
                chunks.append(
                    CodeChunk(
                        content=combined, filename=filename,
                        language="markdown", chunk_type="section",
                        project=project,
                        function_name=heading.lstrip("#").strip(),
                        start_line=offset, end_line=end_line,
                    )
                )

            heading = sections[i]
            body = sections[i + 1] if i + 1 < len(sections) else ""
            offset = "".join(sections[:i]).count("\n") + 1

        # Last section
        if heading:
            combined = f"{heading}\n{body}".strip()
            end_line = offset + combined.count("\n")
            chunks.append(
                CodeChunk(
                    content=combined, filename=filename,
                    language="markdown", chunk_type="section",
                    project=project,
                    function_name=heading.lstrip("#").strip(),
                    start_line=offset, end_line=end_line,
                )
            )
        return chunks

    # ── Generic (JSON, YAML, TOML, .env) ────────────────────────────

    def _chunk_generic(
        self, source: str, filename: str, lang: str, project: str
    ) -> list[CodeChunk]:
        """Chunk config / data / unsupported-AST files with code-aware splitting.

        Uses hierarchical separators (class/def/double-newline) to find
        natural boundaries, targeting 500-800 tokens per chunk with
        100-150 token overlap for context continuity.
        """
        # Small files or JSON: return as single chunk
        if len(source) < 2000 or lang == "json":
            return [
                CodeChunk(
                    content=source[:8000], filename=filename,
                    language=lang, chunk_type="module",
                    project=project, start_line=1,
                    end_line=source.count("\n") + 1,
                )
            ]

        # Try code-aware splitting first
        segments: list[str] = [source]
        for sep in CODE_SEPARATORS:
            new_segments: list[str] = []
            for seg in segments:
                if len(seg) > GENERIC_CHUNK_MAX_CHARS:
                    parts = seg.split(sep)
                    for i, part in enumerate(parts):
                        # Re-attach separator to the start of each part (except first)
                        text = (sep + part) if i > 0 else part
                        new_segments.append(text)
                else:
                    new_segments.append(seg)
            segments = new_segments

        # Merge small adjacent segments and split oversized ones
        chunks: list[CodeChunk] = []
        current_text = ""
        line_offset = 1

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # If adding this segment stays within budget, accumulate
            if len(current_text) + len(segment) + 1 <= GENERIC_CHUNK_MAX_CHARS:
                current_text = f"{current_text}\n{segment}" if current_text else segment
            else:
                # Flush current buffer as a chunk
                if current_text:
                    end_line = line_offset + current_text.count("\n")
                    chunks.append(
                        CodeChunk(
                            content=current_text, filename=filename,
                            language=lang, chunk_type="section",
                            project=project,
                            start_line=line_offset,
                            end_line=end_line,
                        )
                    )
                    # Advance line offset, accounting for overlap
                    overlap_lines = current_text[-GENERIC_CHUNK_OVERLAP_CHARS:].count("\n")
                    line_offset = end_line - overlap_lines + 1

                # Start new chunk (with overlap from previous)
                if chunks and GENERIC_CHUNK_OVERLAP_CHARS > 0:
                    overlap = current_text[-GENERIC_CHUNK_OVERLAP_CHARS:]
                    current_text = f"{overlap}\n{segment}"
                else:
                    current_text = segment

                # Handle oversized segments by hard-splitting
                while len(current_text) > GENERIC_CHUNK_MAX_CHARS:
                    split_point = current_text.rfind("\n", 0, GENERIC_CHUNK_MAX_CHARS)
                    if split_point < GENERIC_CHUNK_MAX_CHARS // 2:
                        split_point = GENERIC_CHUNK_MAX_CHARS
                    chunk_text = current_text[:split_point]
                    end_line = line_offset + chunk_text.count("\n")
                    chunks.append(
                        CodeChunk(
                            content=chunk_text, filename=filename,
                            language=lang, chunk_type="section",
                            project=project,
                            start_line=line_offset,
                            end_line=end_line,
                        )
                    )
                    overlap = chunk_text[-GENERIC_CHUNK_OVERLAP_CHARS:]
                    current_text = overlap + current_text[split_point:]
                    overlap_lines = overlap.count("\n")
                    line_offset = end_line - overlap_lines + 1

        # Flush remaining
        if current_text.strip():
            chunks.append(
                CodeChunk(
                    content=current_text.strip(), filename=filename,
                    language=lang, chunk_type="section",
                    project=project,
                    start_line=line_offset,
                    end_line=line_offset + current_text.count("\n"),
                )
            )

        return chunks

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _find_child(node: Any, child_type: str) -> Any | None:
        """Find the first direct child of *node* with the given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def _find_child_text(node: Any, child_type: str, source: str) -> str:
        """Return the source text of the first child matching *child_type*."""
        for child in node.children:
            if child.type == child_type:
                return source[child.start_byte : child.end_byte]
        return ""

    @staticmethod
    def _find_block_end(source: str, start: int) -> int:
        """Find the closing brace of a JS block starting near *start*."""
        depth = 0
        found_open = False
        for i in range(start, len(source)):
            if source[i] == "{":
                depth += 1
                found_open = True
            elif source[i] == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i + 1
        return len(source)
