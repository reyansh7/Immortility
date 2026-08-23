"""Phase 2B — tool ecosystem: git, documents, docker, databases, command hardening."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.harness import last_turn, reset_for_tests
from core.pending_action import (
    is_destructive_invocation,
    needs_confirmation,
    with_user_confirmation,
)
from tools.command_tool import (
    CLASS_DESTRUCTIVE,
    CLASS_MUTATING,
    CLASS_READONLY,
    CommandTool,
    classify_command,
)
from tools.database_tool import (
    classify_sql,
    db_execute,
    db_query,
    list_connections,
    reset_db_config_cache,
)
from tools.document_tool import extract_document
from tools.git_tool import GitTool
from tools.tool_registry import ToolRegistry, discover_tools, setup_registry


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _git_available() -> bool:
    return shutil.which("git") is not None


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    if not _git_available():
        pytest.skip("git is not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("IMMORTILITY_GIT_ALLOWED_ROOTS", str(repo))
    import os

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    def git(*args: str) -> None:
        subprocess.check_call(
            ["git", *args],
            cwd=repo,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        git("init", "-b", "main")
    except subprocess.CalledProcessError:
        git("init")
        git("checkout", "-b", "main")
    git("config", "user.email", "immortility-test@example.com")
    git("config", "user.name", "Immortility Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "init")
    return repo


@pytest.fixture
def registry():
    ToolRegistry.reset_instance()
    reg = setup_registry()
    yield reg
    ToolRegistry.reset_instance()


# ── Single execution engine ─────────────────────────────────────────────────


def test_git_and_docker_do_not_spawn_their_own_subprocess():
    for rel in ("tools/git_tool.py", "tools/docker_tool.py"):
        src = Path(rel).read_text(encoding="utf-8")
        assert "import subprocess" not in src
        assert "subprocess.Popen" not in src
        assert "subprocess.run" not in src
        assert "Popen(" not in src
        assert "CommandTool" in src


def test_no_task_specific_2b_agents():
    for name in ("git_agent", "pdf_agent", "docker_agent", "database_agent", "docx_agent"):
        assert not Path("agents", f"{name}.py").exists()
        assert not Path("tools", f"{name}.py").exists()
    import tools.git_tool as git_mod
    import tools.document_tool as doc_mod
    import tools.docker_tool as dock_mod
    import tools.database_tool as db_mod

    for mod in (git_mod, doc_mod, dock_mod, db_mod):
        assert not hasattr(mod, "GitAgent")
        assert not hasattr(mod, "PDFAgent")
        assert not hasattr(mod, "DockerAgent")
        assert not hasattr(mod, "DatabaseAgent")


def test_git_goes_through_command_tool(git_repo, monkeypatch):
    calls: list[list[str]] = []
    real = CommandTool.run_argv

    def wrapped(argv, **kwargs):
        calls.append([str(a) for a in argv])
        return real(argv, **kwargs)

    monkeypatch.setattr(CommandTool, "run_argv", staticmethod(wrapped))
    result = GitTool.status(cwd=str(git_repo))
    assert result["status"] == "success"
    assert calls, "git_status must call CommandTool.run_argv"
    assert all(c[0] == "git" for c in calls)


# ── Command hardening ───────────────────────────────────────────────────────


def test_classify_command_readonly_mutating_destructive():
    assert classify_command("git status") == CLASS_READONLY
    assert classify_command("git diff") == CLASS_READONLY
    assert classify_command("git add README.md") == CLASS_MUTATING
    assert classify_command("git commit -m hi") == CLASS_MUTATING
    assert classify_command("git reset --hard HEAD") == CLASS_DESTRUCTIVE
    assert classify_command("git push --force") == CLASS_DESTRUCTIVE
    assert classify_command("git branch -D old") == CLASS_DESTRUCTIVE
    assert classify_command(r"git -C C:\repo reset --hard HEAD") == CLASS_DESTRUCTIVE
    assert classify_command("echo hello") == CLASS_READONLY


def test_command_timeout_kills_process():
    from core.execution_kernel import ExecutionKernel

    ExecutionKernel.get().reset_cancel()
    result = CommandTool.run_argv(
        [sys.executable, "-c", "import time; time.sleep(8)"],
        timeout=0.4,
    )
    assert result["status"] == "error"
    assert result["error_code"] == "TIMEOUT"
    assert result["timed_out"] is True


def test_command_cancellation():
    from core.execution_kernel import ExecutionKernel

    kernel = ExecutionKernel.get()
    kernel.reset_cancel()

    def cancel_soon():
        time.sleep(0.25)
        kernel.cancel_current()

    threading.Thread(target=cancel_soon, daemon=True).start()
    result = CommandTool.run_argv(
        [sys.executable, "-c", "import time; time.sleep(8)"],
        timeout=10.0,
    )
    kernel.reset_cancel()
    assert result["status"] == "error"
    assert result["cancelled"] is True
    assert result["error_code"] == "CANCELLED"


def test_command_output_limit():
    result = CommandTool.run_argv(
        [sys.executable, "-c", "print('x' * 5000)"],
        timeout=10.0,
        max_output=200,
    )
    assert result["status"] == "success"
    assert result["truncated"] is True
    assert len(result["stdout"]) < 800


def test_command_invalid_cwd():
    result = CommandTool.run_command("echo hi", cwd=__file__ + ".missing")
    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_CWD"


def test_run_command_still_requires_confirmation():
    assert needs_confirmation("run_command", {"cmd": "echo hi"})
    assert needs_confirmation("run_command", {"cmd": "git reset --hard HEAD"})
    msg = __import__("core.pending_action", fromlist=["format_confirmation_message"]).format_confirmation_message(
        "run_command", {"cmd": "git reset --hard HEAD"}
    )
    assert "DESTRUCTIVE" in msg


def test_execution_trace_records_command():
    reset_for_tests()
    CommandTool.run_argv([sys.executable, "-c", "print(1)"], timeout=10.0)
    row = last_turn()
    assert row is not None
    assert row["kind"] == "command"


# ── Git primitives ──────────────────────────────────────────────────────────


def test_git_read_operations_structured(git_repo):
    st = GitTool.status(cwd=str(git_repo))
    assert st["status"] == "success"
    assert st["branch"] in {"main", "master"}
    assert st["clean"] is True
    assert isinstance(st["files"], list)

    log = GitTool.log(cwd=str(git_repo), max_count=5)
    assert log["status"] == "success"
    assert log["commits"]
    assert log["commits"][0]["subject"] == "init"
    assert "sha" in log["commits"][0]

    shown = GitTool.show(cwd=str(git_repo), rev="HEAD")
    assert shown["status"] == "success"
    assert shown["subject"] == "init"

    branches = GitTool.branch(cwd=str(git_repo))
    assert branches["status"] == "success"
    names = {b["name"] for b in branches["branches"]}
    assert st["branch"] in names

    diff = GitTool.diff(cwd=str(git_repo))
    assert diff["status"] == "success"
    assert diff["patch"] == "" or isinstance(diff["patch"], str)

    remote = GitTool.remote(cwd=str(git_repo))
    assert remote["status"] == "success"


def test_git_read_skips_confirmation():
    assert not needs_confirmation("git_status", {"cwd": "."})
    assert not needs_confirmation("git_diff", {})
    assert not needs_confirmation("git_log", {})
    assert not needs_confirmation("git_show", {"rev": "HEAD"})
    assert not needs_confirmation("git_branch", {})
    assert not needs_confirmation("git_remote", {})
    assert not needs_confirmation("git_stash", {"action": "list"})


def test_git_modifications_require_confirmation(git_repo):
    assert needs_confirmation("git_add", {"paths": ["README.md"], "cwd": str(git_repo)})
    assert needs_confirmation("git_commit", {"message": "x", "cwd": str(git_repo)})
    assert needs_confirmation("git_checkout", {"target": "main"})
    assert needs_confirmation("git_push", {"remote": "origin"})
    assert needs_confirmation("git_reset", {"mode": "mixed"})
    assert needs_confirmation("git_stash", {"action": "push"})

    (git_repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    added = GitTool.add(paths=["extra.txt"], cwd=str(git_repo))
    assert added["status"] == "success"
    committed = GitTool.commit(message="add extra", cwd=str(git_repo))
    assert committed["status"] == "success"
    assert committed["sha"]
    st = GitTool.status(cwd=str(git_repo))
    assert st["clean"] is True


def test_git_destructive_refused_without_flag(git_repo):
    assert is_destructive_invocation("git_reset", {"mode": "hard"})
    assert needs_confirmation("git_reset", {"mode": "hard"})
    refused = GitTool.reset(mode="hard", target="HEAD", cwd=str(git_repo))
    assert refused["status"] == "error"
    assert refused["error_code"] == "DESTRUCTIVE_CONFIRMATION_REQUIRED"

    pushed = GitTool.push(cwd=str(git_repo), force=True)
    assert pushed["status"] == "error"
    assert pushed["error_code"] == "DESTRUCTIVE_CONFIRMATION_REQUIRED"

    deleted = GitTool.branch(cwd=str(git_repo), delete="no-such-branch")
    assert deleted["status"] == "error"
    assert deleted["error_code"] == "DESTRUCTIVE_CONFIRMATION_REQUIRED"

    # User confirmation injects the flag; still no silent path.
    approved = with_user_confirmation("git_reset", {"mode": "hard", "cwd": str(git_repo)})
    assert approved.get("confirm_destructive") is True
    ran = GitTool.reset(mode="hard", target="HEAD", cwd=str(git_repo), confirm_destructive=True)
    assert ran["status"] == "success"


def test_git_repo_boundary_enforced(tmp_path, git_repo):
    outsider = tmp_path / "other"
    outsider.mkdir()
    subprocess.check_call(
        ["git", "init", "-b", "main"],
        cwd=outsider,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result = GitTool.status(cwd=str(outsider))
    assert result["status"] == "error"
    assert result["error_code"] == "GIT_BOUNDARY"

    escaped = GitTool.add(paths=["../secret.txt"], cwd=str(git_repo))
    assert escaped["status"] == "error"
    assert escaped["error_code"] == "GIT_BOUNDARY"


def test_git_failure_is_actionable(git_repo):
    result = GitTool.show(rev="this-rev-does-not-exist-xyz", cwd=str(git_repo))
    assert result["status"] == "error"
    assert result["error_code"] in {"GIT_FAILED", "TIMEOUT"}
    assert result["message"]


def test_git_kernel_trace(git_repo):
    reset_for_tests()

    async def _run():
        from core.execution_kernel import get_kernel

        kernel = get_kernel()
        kernel.reset_cancel()
        return await kernel.run_tool("git_status", {"cwd": str(git_repo)}, use_cache=False)

    result = asyncio.run(_run())
    assert result.ok
    payload = json.loads(result.value)
    assert payload["result"]["status"] == "success"
    row = last_turn()
    assert row is not None
    assert row["kind"] in {"tool", "command"}


# ── Documents ───────────────────────────────────────────────────────────────


def test_extract_pdf_docx_pptx_xlsx_csv(tmp_path):
    fitz = pytest.importorskip("fitz")
    docx = pytest.importorskip("docx")
    pptx = pytest.importorskip("pptx")
    openpyxl = pytest.importorskip("openpyxl")

    pdf_path = tmp_path / "a.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF_MARKER_2B")
    doc.save(pdf_path)
    doc.close()
    pdf = extract_document(str(pdf_path))
    assert pdf["status"] == "success"
    assert "PDF_MARKER_2B" in pdf["text"]

    docx_path = tmp_path / "a.docx"
    d = docx.Document()
    d.add_paragraph("DOCX_MARKER_2B")
    d.save(docx_path)
    dx = extract_document(str(docx_path))
    assert dx["status"] == "success"
    assert "DOCX_MARKER_2B" in dx["text"]

    pptx_path = tmp_path / "a.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "PPTX_MARKER_2B" if slide.shapes.title else ""
    box = slide.shapes.add_textbox(0, 0, 1_000_000, 200_000)
    box.text_frame.text = "PPTX_MARKER_2B"
    prs.save(pptx_path)
    px = extract_document(str(pptx_path))
    assert px["status"] == "success"
    assert "PPTX_MARKER_2B" in px["text"]

    xlsx_path = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "XLSX_MARKER_2B"
    wb.save(xlsx_path)
    xl = extract_document(str(xlsx_path))
    assert xl["status"] == "success"
    assert xl["sheets"][0]["rows"][0][0] == "XLSX_MARKER_2B"

    csv_path = tmp_path / "a.csv"
    csv_path.write_text("name,value\nalpha,1\n", encoding="utf-8")
    csv_doc = extract_document(str(csv_path))
    assert csv_doc["status"] == "success"
    assert csv_doc["headers"][0] == "name"
    assert csv_doc["rows"][1][0] == "alpha"


def test_extract_missing_file(tmp_path):
    result = extract_document(str(tmp_path / "nope.pdf"))
    assert result["status"] == "error"
    assert result["error_code"] == "NOT_FOUND"


# ── Docker ──────────────────────────────────────────────────────────────────


def test_docker_readonly_uses_command_engine(monkeypatch):
    from tools.docker_tool import DockerTool

    calls = []

    def fake_run_argv(argv, **kwargs):
        calls.append([str(a) for a in argv])
        if argv[1] == "ps":
            stdout = json.dumps({"ID": "abc", "Image": "nginx"}) + "\n"
        elif argv[1] == "images":
            stdout = json.dumps({"Repository": "nginx"}) + "\n"
        elif argv[1] == "inspect":
            stdout = json.dumps([{"Id": "abc"}])
        elif argv[1] == "logs":
            stdout = "line1\n"
        else:
            stdout = json.dumps({"ServerVersion": "test"})
        return {
            "status": "success",
            "stdout": stdout,
            "stderr": "",
            "returncode": 0,
            "timed_out": False,
            "cancelled": False,
        }

    monkeypatch.setattr("tools.docker_tool.CommandTool.run_argv", fake_run_argv)
    ps = DockerTool.ps()
    assert ps.get("status") == "success", ps
    assert DockerTool.images()["status"] == "success"
    assert DockerTool.inspect("abc")["status"] == "success"
    assert DockerTool.logs("abc")["status"] == "success"
    assert DockerTool.info()["status"] == "success"
    assert calls
    assert all(c[0] == "docker" for c in calls)
    assert not needs_confirmation("docker_ps", {})
    assert not needs_confirmation("docker_images", {})
    assert not needs_confirmation("docker_info", {})


def test_docker_rm_requires_confirmation_and_flag():
    from tools.docker_tool import DockerTool

    assert needs_confirmation("docker_rm", {"target": "abc"})
    assert is_destructive_invocation("docker_rm", {"target": "abc"})
    refused = DockerTool.rm("abc")
    assert refused["status"] == "error"
    assert refused["error_code"] == "DESTRUCTIVE_CONFIRMATION_REQUIRED"


# ── Databases ───────────────────────────────────────────────────────────────


def test_database_rejects_unknown_connection(monkeypatch):
    monkeypatch.delenv("IMMORTILITY_SQLITE_PATH", raising=False)
    monkeypatch.delenv("IMMORTILITY_POSTGRES_URL", raising=False)
    monkeypatch.delenv("IMMORTILITY_MONGO_URL", raising=False)
    reset_db_config_cache()
    result = db_query("not_a_real_connection", "SELECT 1")
    assert result["status"] == "error"
    assert result["error_code"] == "DB_NOT_CONFIGURED"
    listed = list_connections()
    assert listed["status"] == "success"


def test_sqlite_configured_read_and_write_guard(tmp_path, monkeypatch):
    db_path = tmp_path / "notes.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes (body) VALUES ('hello')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("IMMORTILITY_SQLITE_PATH", str(db_path))
    monkeypatch.delenv("IMMORTILITY_SQLITE_ALLOW_WRITE", raising=False)
    reset_db_config_cache()

    listed = list_connections()
    names = {c["name"] for c in listed["connections"]}
    assert "sqlite_default" in names

    rows = db_query("sqlite_default", "SELECT body FROM notes")
    assert rows["status"] == "success"
    assert rows["rows"][0][0] == "hello"

    blocked = db_query("sqlite_default", "DELETE FROM notes")
    assert blocked["status"] == "error"
    assert blocked["error_code"] == "READ_ONLY_QUERY"

    write_blocked = db_execute("sqlite_default", "INSERT INTO notes (body) VALUES ('x')")
    assert write_blocked["status"] == "error"
    assert write_blocked["error_code"] == "DB_WRITE_NOT_ALLOWED"

    monkeypatch.setenv("IMMORTILITY_SQLITE_ALLOW_WRITE", "1")
    reset_db_config_cache()
    assert needs_confirmation("db_execute", {"connection": "sqlite_default", "sql": "INSERT INTO notes (body) VALUES ('x')"})
    dropped = db_execute("sqlite_default", "DROP TABLE notes")
    assert dropped["status"] == "error"
    assert dropped["error_code"] == "DESTRUCTIVE_CONFIRMATION_REQUIRED"


def test_classify_sql_read_vs_write():
    assert classify_sql("SELECT * FROM t") == "read"
    assert classify_sql("WITH x AS (SELECT 1) SELECT * FROM x") == "read"
    assert classify_sql("DELETE FROM t") == "write"
    assert classify_sql("SELECT 1; DROP TABLE t") == "multi"


def test_db_query_skips_confirmation():
    assert not needs_confirmation("db_query", {"connection": "sqlite_default", "sql": "SELECT 1"})
    assert not needs_confirmation("db_list_connections", {})
    assert not needs_confirmation("extract_document", {"path": "a.pdf"})


# ── Discovery / composition ─────────────────────────────────────────────────


def test_tool_discovery_and_dynamic_composition(registry):
    git_hits = registry.discover("git")
    names = {t["name"] for t in git_hits}
    assert "git_status" in names
    assert "git_commit" in names
    assert "extract_document" not in names

    docs = registry.discover("pdf")
    assert any(t["name"] == "extract_document" for t in docs)

    docker_hits = discover_tools(query="docker")
    assert docker_hits["count"] >= 1
    assert any(t["name"] == "docker_ps" for t in docker_hits["tools"])

    # A previously unseen task can select git + documents + db without a dedicated agent.
    needed = []
    for query in ("git status", "pdf", "sqlite"):
        needed.extend(registry.discover(query))
    combo = {t["name"] for t in needed}
    assert "git_status" in combo
    assert "extract_document" in combo
    assert "db_query" in combo
    assert not needs_confirmation("discover_tools", {"query": "git"})


def test_phase2b_tools_registered(registry):
    have = set(registry.list_tools())
    for name in (
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_branch",
        "git_add",
        "git_commit",
        "extract_document",
        "docker_ps",
        "docker_info",
        "db_query",
        "discover_tools",
        "run_command",
    ):
        assert name in have
        perms = registry.get_permissions(name)
        assert set(perms) >= {"read", "write", "network", "destructive"}
