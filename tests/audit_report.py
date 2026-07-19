#!/usr/bin/env python3
"""
Immortility Phase 2 / 2.5 / 3 audit runner.

Usage: python tests/audit_report.py
Produces JSON report to audit_report.json
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@dataclass
class AuditResult:
    name: str
    phase: str
    status: str  # pass | fail | partial | missing
    message: str = ""
    duration_ms: float = 0


@dataclass
class AuditReport:
    timestamp: str = ""
    results: list[AuditResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add(self, r: AuditResult) -> None:
        self.results.append(r)

    def finalize(self) -> None:
        counts = {"pass": 0, "fail": 0, "partial": 0, "missing": 0}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        self.summary = counts


def run_check(name: str, phase: str, fn) -> AuditResult:
    t0 = time.perf_counter()
    try:
        fn()
        ms = (time.perf_counter() - t0) * 1000
        return AuditResult(name, phase, "pass", duration_ms=ms)
    except AssertionError as e:
        ms = (time.perf_counter() - t0) * 1000
        return AuditResult(name, phase, "fail", str(e), ms)
    except ModuleNotFoundError as e:
        ms = (time.perf_counter() - t0) * 1000
        return AuditResult(name, phase, "missing", str(e), ms)
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return AuditResult(name, phase, "fail", f"{e}\n{traceback.format_exc()[:300]}", ms)


def audit_phase2(report: AuditReport, tmp: Path) -> None:
    def indexing():
        from rag.vector_store import VectorStore
        from rag.embeddings import EmbeddingModel
        from rag.indexer import Indexer
        from rag.chunker import Chunker

        store = VectorStore(persist_dir=str(tmp / "vdb"))
        emb = EmbeddingModel()
        indexer = Indexer(store, emb, Chunker())
        f = tmp / "auth.py"
        f.write_text("def login():\n    return 'jwt'\n", encoding="utf-8")
        n = indexer.index_file(f, project="audit")
        assert n >= 1
        assert store.count() >= 1

    def hybrid_search():
        from rag.vector_store import VectorStore
        from rag.embeddings import EmbeddingModel
        from rag.hybrid_search import HybridSearch

        store = VectorStore(persist_dir=str(tmp / "vdb2"))
        emb = EmbeddingModel()
        hs = HybridSearch(store, emb)
        emb_vec = emb.encode(["JWT authentication login"])
        store.add(
            ids=["c1"],
            embeddings=emb_vec,
            documents=["def login(): return jwt_token"],
            metadatas=[{"filename": "auth.py", "project": "audit"}],
        )
        results = hs.search("JWT login", n_results=1)
        assert results

    def context_builder():
        from knowledge.context_builder import ContextBuilder
        from rag.retriever import RetrievalResult

        cb = ContextBuilder()
        ctx = cb.build_from_query(
            "auth",
            [RetrievalResult("def login(): pass", "auth.py", 0.9, "function")],
            project_summary="Test project",
            preferences="",
            memory="",
        )
        assert "auth.py" in ctx

    def memory_layers():
        from memory.memory_manager import MemoryManager
        import tempfile
        mem_dir = tmp / "mem"
        mem_dir.mkdir(exist_ok=True)
        mm = MemoryManager()
        mm.conversation.add_note("audit test hello")
        mm.preferences.set("editor", "vim")
        mm.project.remember_project({"name": "audit", "path": str(tmp)})
        assert mm.conversation.count() >= 1
        assert mm.preferences.count() >= 1

    def docs_retrieval():
        from rag.vector_store import VectorStore
        from rag.embeddings import EmbeddingModel
        store = VectorStore(persist_dir=str(tmp / "docs"), collection_name="documentation")
        emb = EmbeddingModel()
        store.add(
            ids=["d1"],
            embeddings=emb.encode(["FastAPI JWT docs"]),
            documents=["Use OAuth2PasswordBearer for JWT"],
            metadatas=[{"filename": "jwt.md", "project": "fastapi"}],
        )
        hits = store.search(emb.encode_query("JWT"), n_results=1)
        assert hits

    for name, fn in [
        ("Project indexing + ChromaDB", indexing),
        ("Hybrid retrieval (semantic)", hybrid_search),
        ("Context builder", context_builder),
        ("Memory layers", memory_layers),
        ("Documentation store", docs_retrieval),
    ]:
        report.add(run_check(name, "Phase 2", fn))


def audit_phase25(report: AuditReport, tmp: Path) -> None:
    def patch_workflow():
        from editing.coding_workflow import CodingWorkflow
        from editing.patch_generator import PatchOperation
        from unittest.mock import AsyncMock, patch
        import asyncio
        (tmp / "main.py").write_text("x = 1\n", encoding="utf-8")
        wf = CodingWorkflow(project_root=tmp)
        patch_op = PatchOperation(
            path=str(tmp / "utils.py"),
            operation="create_file",
            args={"content": "def helper():\n    return 42\n"},
        )
        with patch.object(wf.patch_generator, "generate_patches", new_callable=AsyncMock) as mg:
            from editing.edit_planner import EditPlan
            with patch.object(wf.edit_planner, "generate_plan", new_callable=AsyncMock) as mp:
                mp.return_value = EditPlan(
                    goal="add utils",
                    files_to_read=[],
                    files_to_edit=["utils.py"],
                    dependencies=[],
                    risks=[],
                    verification_strategy="syntax",
                    symbols_to_modify=[],
                    plan_steps=["Create utils.py"],
                )
                mg.return_value = [patch_op]
                result = asyncio.run(wf.run("Add utils module"))
        assert result.success
        assert (tmp / "utils.py").exists()

    def reflection():
        from editing.reflection_engine import ReflectionEngine
        import asyncio
        state = {"n": 0}

        def edit():
            state["n"] += 1
            return state["n"] >= 2

        def verify():
            return "PASS" if state["n"] >= 2 else "FAIL: retry"

        import asyncio
        engine = ReflectionEngine(max_retries=3)
        ok = asyncio.run(
            engine.execute_with_reflection_sync(edit, verify, lambda: None)
        )
        assert ok

    def tsx_validation():
        from editing.patch_validator import PatchValidator
        tsx = "export default function P() { return <p>Don't</p>; }\n"
        valid, _ = PatchValidator.validate_syntax(tsx, "p.tsx")
        assert valid

    def patch_args():
        from editing.patch_args import normalize_patch_args
        op, args = normalize_patch_args("edit_file", {"target_text": "a", "content": "b"})
        assert op == "edit_file"
        assert args["replacement_text"] == "b"

    for name, fn in [
        ("Coding workflow (create file)", patch_workflow),
        ("Reflection retry", reflection),
        ("TSX syntax validation", tsx_validation),
        ("Patch arg normalization", patch_args),
    ]:
        report.add(run_check(name, "Phase 2.5", fn))


def audit_phase3(report: AuditReport, tmp: Path) -> None:
    def modules_import():
        from core.workflow_engine import WorkflowEngine
        from core.decision_engine import DecisionEngine
        from core.checkpoint_manager import CheckpointManager
        from core.event_bus import EventBus
        assert WorkflowEngine and DecisionEngine

    def state_persistence():
        from core.workflow_state import WorkflowState, WorkflowStep
        db = tmp / "wf.db"
        s = WorkflowState(str(db))
        s.create_workflow("id1", WorkflowStep.PLANNING, {"goal": "g"})
        assert s.get_workflow("id1") is not None

    def pause_resume():
        from core.workflow_state import WorkflowState, WorkflowStep, WorkflowStatus
        db = tmp / "wf2.db"
        s = WorkflowState(str(db))
        s.create_workflow("id2", WorkflowStep.PLANNING, {"goal": "g"})
        s.pause_workflow("id2")
        assert s.get_workflow("id2")["status"] == WorkflowStatus.PAUSED
        s.resume_workflow("id2")
        assert s.get_workflow("id2")["status"] == WorkflowStatus.RUNNING

    def checkpoint():
        from core.checkpoint_manager import CheckpointManager
        f = tmp / "f.py"
        f.write_text("a=1\n", encoding="utf-8")
        ck = CheckpointManager(tmp / "ck")
        path = ck.save("wf", {"goal": "x"}, [str(f)])
        assert Path(path).exists()

    for name, fn in [
        ("Phase 3 modules import", modules_import),
        ("Workflow state persistence", state_persistence),
        ("Pause / resume", pause_resume),
        ("Checkpoint manager", checkpoint),
    ]:
        report.add(run_check(name, "Phase 3", fn))


def audit_mcp(report: AuditReport) -> None:
    mcp_dir = ROOT / "mcp"
    if not mcp_dir.exists():
        report.add(AuditResult("MCP subsystem", "MCP", "missing", "No mcp/ directory — not implemented"))
        return
    report.add(AuditResult("MCP subsystem", "MCP", "missing", "mcp/ exists but no manager/client/server code"))


def audit_integration(report: AuditReport) -> None:
    def main_wiring():
        import main
        assert hasattr(main, "_get_engine")
        assert hasattr(main, "execute_workflow")
        assert hasattr(main, "handle_project_query")

    def workflow_import():
        from core.workflow_engine import WorkflowEngine
        assert WorkflowEngine

    report.add(run_check("main.py integration hooks", "Integration", main_wiring))
    report.add(run_check("WorkflowEngine importable from main path", "Integration", workflow_import))


def main():
    audit_dir = ROOT / ".audit_tmp"
    audit_dir.mkdir(exist_ok=True)
    tmp = audit_dir / "run"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    report = AuditReport(timestamp=time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        audit_phase2(report, tmp)
        audit_phase25(report, tmp)
        audit_phase3(report, tmp)
    finally:
        import gc
        gc.collect()
    audit_mcp(report)
    audit_integration(report)
    report.finalize()

    out = ROOT / "audit_report.json"
    out.write_text(json.dumps({"summary": report.summary, "results": [asdict(r) for r in report.results], "timestamp": report.timestamp}, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report.summary, "output": str(out)}, indent=2))
    return 0 if report.summary.get("fail", 0) == 0 and report.summary.get("missing", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
