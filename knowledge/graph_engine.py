"""Graph Engine for deterministic codebase structural analysis.

Runs Graphify on projects to generate a knowledge graph (AST based) and 
provides an interface for agents and the context builder to query relationships.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GraphEngine:
    """Graphify wrapper over local graph.json with symbol-only lookup."""

    def __init__(self) -> None:
        self._graph_data: dict[str, Any] | None = None
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges_from: dict[str, list[dict[str, Any]]] = {}
        self._edges_to: dict[str, list[dict[str, Any]]] = {}
        self._project_path: Path | None = None

    def generate_and_load(self, project_path: str | Path) -> bool:
        """Run graphify in the background and load the resulting graph."""
        self._project_path = Path(project_path).resolve()
        logger.info(f"Generating knowledge graph for {self._project_path}...")
        
        try:
            # Run graphify via uvx
            result = subprocess.run(
                ["uvx", "--from", "graphifyy", "graphify", ".", "--code-only"],
                cwd=str(self._project_path),
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0 and "error" in result.stderr.lower():
                logger.warning(f"Graphify warning/error: {result.stderr}")
                
            graph_json_path = self._project_path / "graphify-out" / "graph.json"
            if not graph_json_path.exists():
                logger.error(f"Graph generation failed: {graph_json_path} not found.")
                return False
                
            self.load_graph(graph_json_path)
            logger.info(f"Successfully loaded graph with {len(self._nodes)} nodes.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to run graphify: {e}")
            return False

    def load_graph(self, json_path: str | Path) -> None:
        """Load graph.json into memory."""
        path = Path(json_path)
        if not path.exists():
            return
            
        with path.open("r", encoding="utf-8") as f:
            self._graph_data = json.load(f)
            
        self._nodes.clear()
        self._edges_from.clear()
        self._edges_to.clear()
        
        for node in self._graph_data.get("nodes", []):
            node_id = node.get("id")
            if node_id:
                self._nodes[node_id] = node
                
        for edge in self._graph_data.get("links", []):
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt:
                self._edges_from.setdefault(src, []).append(edge)
                self._edges_to.setdefault(tgt, []).append(edge)

    def is_loaded(self) -> bool:
        return self._graph_data is not None

    def get_node_by_label(self, label_substring: str) -> list[dict[str, Any]]:
        """Find nodes containing the substring in their label or source_file."""
        results = []
        lower_sub = label_substring.lower()
        for node in self._nodes.values():
            label = node.get("label", "").lower()
            src = node.get("source_file", "").lower()
            if lower_sub in label or lower_sub in src:
                results.append(node)
        return results

    @staticmethod
    def _sanitize_symbol(symbol: str) -> str:
        # Accept only simple file/symbol strings; reject query-language-like payloads.
        allowed = "._-/:\\ "
        cleaned = "".join(ch for ch in (symbol or "") if ch.isalnum() or ch in allowed)
        return cleaned.strip()[:120]

    def query_relationships(self, query: str) -> str:
        """Lookup graph relationships for a single symbol or filename."""
        if not self.is_loaded():
            return "Knowledge graph is not loaded for this project."

        target_symbol = self._sanitize_symbol(query)
        if not target_symbol:
            return "No valid symbol provided."

        nodes = self.get_node_by_label(target_symbol)
        if not nodes:
            return f"No nodes found matching '{target_symbol}' in the graph."
            
        # Just take the best match (first one)
        target = nodes[0]
        node_id = target.get("id")
        
        lines = [f"Graph Analysis for: {target.get('label')} ({target.get('source_file')})"]
        
        # What this node depends on (outbound edges)
        outbound = self._edges_from.get(node_id, [])
        if outbound:
            lines.append("\nDepends On (Outbound):")
            for edge in outbound[:15]:
                tgt_id = edge.get("target")
                tgt_node = self._nodes.get(tgt_id, {})
                rel = edge.get("type", "references")
                lines.append(f"  - [{rel}] -> {tgt_node.get('label', tgt_id)}")
        
        # What depends on this node (inbound edges)
        inbound = self._edges_to.get(node_id, [])
        if inbound:
            lines.append("\nDepended On By (Inbound):")
            for edge in inbound[:15]:
                src_id = edge.get("source")
                src_node = self._nodes.get(src_id, {})
                rel = edge.get("type", "references")
                lines.append(f"  - {src_node.get('label', src_id)} -> [{rel}]")
                
        return "\n".join(lines)
