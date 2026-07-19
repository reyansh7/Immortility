"""Hierarchical repository memory built from the AST knowledge graph."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from knowledge.ast_extractor import ASTExtractor
from knowledge.knowledge_graph_db import KnowledgeGraphDB
from rag.security_filters import should_index_path

logger = logging.getLogger(__name__)


class HierarchicalMemory:
    """File → folder → repository summaries stored in KnowledgeGraphDB."""

    def __init__(
        self,
        db: KnowledgeGraphDB | None = None,
        extractor: ASTExtractor | None = None,
    ) -> None:
        self.db = db or KnowledgeGraphDB()
        self.extractor = extractor or ASTExtractor()

    def ingest_file(self, filepath: str | Path, project: str) -> bool:
        """Parse one file into the graph and refresh its structured summary."""
        path = Path(filepath)
        if not path.is_file() or not should_index_path(path):
            return False
        structure = self.extractor.extract(path)
        if structure is None:
            return False
        summary = structure.to_summary()
        self.db.upsert_file_graph(
            project=project,
            path=structure.path,
            language=structure.language,
            sha256=structure.sha256,
            symbols=structure.symbols,
            dependencies=structure.dependencies,
            calls=structure.calls,
            summary=summary,
        )
        return True

    def ingest_project(self, project_root: str | Path, project: str | None = None) -> dict:
        """Walk project, ingest supported code files, rebuild folder/repo summaries."""
        root = Path(project_root).resolve()
        name = project or root.name
        count = 0
        for fp in root.rglob("*"):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
                continue
            if not should_index_path(fp):
                continue
            if self.ingest_file(fp, name):
                count += 1
        folder_n = self.rebuild_folder_summaries(name, root)
        repo = self.rebuild_repo_summary(name, root)
        self._seed_project_facts(name, root)
        return {
            "project": name,
            "files_ingested": count,
            "folders": folder_n,
            "repo_summary_chars": len(repo),
        }

    def rebuild_folder_summaries(self, project: str, root: Path | None = None) -> int:
        by_folder: dict[str, list[dict]] = defaultdict(list)
        for item in self.db.list_file_summaries(project):
            p = Path(item["path"])
            try:
                rel = p.relative_to(root) if root and str(p).startswith(str(root)) else Path(p.name)
            except ValueError:
                rel = Path(p.name)
            folder = str(rel.parent).replace("\\", "/") if str(rel.parent) != "." else "."
            by_folder[folder].append(item)

        for folder, items in by_folder.items():
            exports = []
            for it in items[:12]:
                exports.extend(it.get("exports", [])[:3])
            summary = (
                f"Folder `{folder}` contains {len(items)} indexed file(s). "
                f"Key exports: {', '.join(exports[:12]) or 'n/a'}."
            )
            self.db.upsert_folder_summary(project, folder, summary)
        return len(by_folder)

    def rebuild_repo_summary(self, project: str, root: Path | None = None) -> str:
        files = self.db.list_file_summaries(project)
        folders = self.db.get_folder_summaries(project)
        facts = self.db.list_facts(project)
        lines = [f"Repository `{project}` structural overview."]
        if root:
            lines.append(f"Root: {root}")
        lines.append(f"Indexed files: {len(files)}. Folders: {len(folders)}.")
        if facts:
            lines.append(
                "Facts: " + "; ".join(f"{f['key']}={f['value']}" for f in facts[:12])
            )
        # top exports across repo
        exports: list[str] = []
        for f in files[:40]:
            exports.extend(f.get("exports", [])[:2])
        if exports:
            lines.append("Notable symbols: " + ", ".join(exports[:25]))
        for folder in folders[:8]:
            lines.append(folder["summary"])
        summary = " ".join(lines)
        # Cap ~500 words
        words = summary.split()
        if len(words) > 500:
            summary = " ".join(words[:500])
        self.db.upsert_repo_summary(project, summary)
        return summary

    def _seed_project_facts(self, project: str, root: Path) -> None:
        if (root / "package.json").exists():
            self.db.set_fact(project, "package_manager", "npm")
            try:
                text = (root / "package.json").read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                text = ""
            if "next" in text:
                self.db.set_fact(project, "framework", "Next.js")
            if "react" in text:
                self.db.set_fact(project, "ui", "React")
        if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
            self.db.set_fact(project, "language", "Python")
            req = ""
            for name in ("requirements.txt", "pyproject.toml"):
                p = root / name
                if p.exists():
                    try:
                        req += p.read_text(encoding="utf-8", errors="ignore").lower()
                    except OSError:
                        pass
            if "fastapi" in req:
                self.db.set_fact(project, "framework", "FastAPI")
            if "django" in req:
                self.db.set_fact(project, "framework", "Django")
            if "jwt" in req or "pyjwt" in req:
                self.db.set_fact(project, "auth", "JWT")
            if "sqlalchemy" in req:
                self.db.set_fact(project, "orm", "SQLAlchemy")

    def lookup_symbol(self, name: str, project: str | None = None) -> list[dict]:
        return self.db.lookup_symbol(name, project=project)
