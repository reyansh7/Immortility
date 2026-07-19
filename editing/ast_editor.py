"""AST-based editing with tree-sitter, regex fallback."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ASTEditor:
    """Python symbol renaming via tree-sitter with regex fallback."""

    def __init__(self) -> None:
        self._parser: Any = None
        self._initialized = False

    def _init_parser(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_python as tspython

            self._parser = Parser(Language(tspython.language()))
        except Exception as exc:
            logger.warning("Tree-sitter unavailable for AST editing: %s", exc)
            self._parser = None

    def rename_symbol(self, content: str, old_name: str, new_name: str) -> str | None:
        """Rename a symbol preserving word boundaries. Returns new content or None."""
        self._init_parser()
        if self._parser is not None:
            try:
                return self._rename_with_ast(content, old_name, new_name)
            except Exception as exc:
                logger.warning("AST rename failed, using regex: %s", exc)
        return self._rename_with_regex(content, old_name, new_name)

    def _rename_with_ast(self, content: str, old_name: str, new_name: str) -> str:
        tree = self._parser.parse(content.encode("utf-8"))
        positions: list[tuple[int, int]] = []

        def walk(node: Any) -> None:
            if node.type == "identifier":
                text = content[node.start_byte : node.end_byte]
                if text == old_name:
                    positions.append((node.start_byte, node.end_byte))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        if not positions:
            return content

        result = content
        for start, end in sorted(positions, reverse=True):
            result = result[:start] + new_name + result[end:]
        return result

    @staticmethod
    def _rename_with_regex(content: str, old_name: str, new_name: str) -> str:
        return re.sub(rf"\b{re.escape(old_name)}\b", new_name, content)
