"""Phase 2: AST knowledge graph + hierarchical memory (no vector/LLM required)."""

from __future__ import annotations

from pathlib import Path

from knowledge.ast_extractor import ASTExtractor
from knowledge.hierarchical_memory import HierarchicalMemory
from knowledge.knowledge_graph_db import KnowledgeGraphDB


def _write_sample_project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "services").mkdir(parents=True)
    (root / "services" / "auth.py").write_text(
        """
from utils.tokens import mint_token

def login(user: str) -> str:
    token = mint_token(user)
    return token

def logout(user: str) -> None:
    return None
""".lstrip(),
        encoding="utf-8",
    )
    (root / "utils").mkdir()
    (root / "utils" / "tokens.py").write_text(
        """
def mint_token(user: str) -> str:
    return f"tok-{user}"

def verify_token(token: str) -> bool:
    return token.startswith("tok-")
""".lstrip(),
        encoding="utf-8",
    )
    return root


def test_ast_extractor_python_symbols_and_deps(tmp_path):
    root = _write_sample_project(tmp_path)
    ext = ASTExtractor()
    structure = ext.extract(root / "services" / "auth.py")
    assert structure is not None
    names = {s["name"] for s in structure.symbols}
    assert "login" in names
    assert "logout" in names
    mods = {d["source_module"] for d in structure.dependencies}
    assert any("tokens" in m for m in mods)
    callees = {c["callee"] for c in structure.calls if c["caller"] == "login"}
    assert "mint_token" in callees


def test_lookup_symbol_from_sqlite_without_vector_or_llm(tmp_path):
    root = _write_sample_project(tmp_path)
    db = KnowledgeGraphDB(tmp_path / "kg.db")
    mem = HierarchicalMemory(db=db)
    report = mem.ingest_project(root, project="demo")
    assert report["files_ingested"] >= 2

    hits = mem.lookup_symbol("login", project="demo")
    assert hits, "login should resolve from SQLite graph"
    hit = hits[0]
    assert hit["file_path"].replace("\\", "/").endswith("services/auth.py")
    assert hit["kind"] == "function"
    dep_text = " ".join(
        f"{d.get('source_module','')} {d.get('imported_name','')}"
        for d in hit["dependencies"]
    )
    assert "token" in dep_text.lower()
    assert "mint_token" in hit["callees"]

    # Defining file for mint_token
    mint_hits = mem.lookup_symbol("mint_token", project="demo")
    assert mint_hits
    assert mint_hits[0]["file_path"].replace("\\", "/").endswith("utils/tokens.py")


def test_hierarchical_summaries_and_facts(tmp_path):
    root = _write_sample_project(tmp_path)
    (root / "requirements.txt").write_text("fastapi\npyjwt\n", encoding="utf-8")
    db = KnowledgeGraphDB(tmp_path / "kg.db")
    mem = HierarchicalMemory(db=db)
    mem.ingest_project(root, project="demo")

    files = db.list_file_summaries("demo")
    assert len(files) >= 2
    assert all("purpose" in f for f in files)

    folders = db.get_folder_summaries("demo")
    assert folders

    repo = db.get_repo_summary("demo")
    assert repo
    assert len(repo.split()) <= 500

    facts = db.list_facts("demo")
    keys = {f["key"] for f in facts}
    assert "language" in keys or "framework" in keys or "auth" in keys


def test_reflections_persist(tmp_path):
    db = KnowledgeGraphDB(tmp_path / "kg.db")
    db.add_reflection(
        task="fix login",
        what_broke="NameError on mint_token",
        what_fixed_it="imported mint_token",
        files_modified=["services/auth.py"],
        project="demo",
    )
    rows = db.list_reflections(project="demo")
    assert len(rows) == 1
    assert rows[0]["what_broke"].startswith("NameError")
    assert "services/auth.py" in rows[0]["files_modified"]


def test_js_extractor_imports(tmp_path):
    f = tmp_path / "button.tsx"
    f.write_text(
        """
import React from "react";
import { useAuth } from "./auth";

export function Button() {
  const a = useAuth();
  return <button>{a ? "out" : "in"}</button>;
}
""".lstrip(),
        encoding="utf-8",
    )
    structure = ASTExtractor().extract(f)
    assert structure is not None
    names = {s["name"] for s in structure.symbols}
    assert "Button" in names
    mods = " ".join(d.get("source_module", "") for d in structure.dependencies)
    assert "react" in mods.lower() or "./auth" in mods
