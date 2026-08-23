"""
Phase 1.5 tests — intelligent code editing engine.

Run with: pytest tests/test_phase15.py -v
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from editing.file_editor import FileEditor
from editing.patch_validator import PatchValidator
from editing.reflection_engine import ReflectionEngine
from editing.verifier import Verifier
from tools.file_tool import FileTool


@pytest.fixture
def sample_project(tmp_path):
    """Minimal project for editing tests."""
    (tmp_path / "auth.py").write_text(
        "class AuthManager:\n"
        "    def login(self, username: str, password: str) -> str:\n"
        '        """Authenticate user and return JWT."""\n'
        '        return "jwt_token"\n\n'
        "    def verify_token(self, token: str) -> dict:\n"
        '        return {"user": "test"}\n',
        encoding="utf-8",
    )
    (tmp_path / "middleware.py").write_text(
        "from auth import AuthManager\n\n"
        "def auth_middleware(request):\n"
        '    token = request.headers.get("Authorization")\n'
        '    return AuthManager("secret").login("u", "p")\n',
        encoding="utf-8",
    )
    (tmp_path / "detect.py").write_text(
        "from ultralytics import YOLO\n\n"
        "class YOLODetector:\n"
        "    def __init__(self, model_path: str = 'yolov8n.pt'):\n"
        "        self.model = YOLO(model_path)\n\n"
        "    def detect(self, image):\n"
        "        return self.model(image)\n",
        encoding="utf-8",
    )
    return tmp_path


# ── Test 4: Rename login → authenticate (small patch) ─────────────


@pytest.mark.asyncio
async def test_4_rename_login_to_authenticate(sample_project):
    from editing.coding_workflow import run_symbol_rename

    auth_before = (sample_project / "auth.py").read_text(encoding="utf-8")
    assert "def login(" in auth_before
    assert "def authenticate(" not in auth_before

    result = await run_symbol_rename(sample_project, "login", "authenticate")
    assert result.success

    auth_after = (sample_project / "auth.py").read_text(encoding="utf-8")
    assert "def authenticate(" in auth_after
    assert "def login(" not in auth_after

    mw = (sample_project / "middleware.py").read_text(encoding="utf-8")
    assert ".authenticate(" in mw
    assert ".login(" not in mw

    assert Verifier.verify_file(str(sample_project / "auth.py")) == "PASS"


# ── Test 5: Add logging to detect.py (insert only) ──────────────────


@pytest.mark.asyncio
async def test_5_add_logging_to_detect(sample_project):
    from editing.coding_workflow import CodingWorkflow

    detect_path = sample_project / "detect.py"
    before = detect_path.read_text(encoding="utf-8")
    line_count_before = len(before.splitlines())

    workflow = CodingWorkflow(project_root=sample_project)
    result = await workflow.run("Add logging to detect.py")

    assert result.success
    after = detect_path.read_text(encoding="utf-8")
    assert "import logging" in after
    assert "logger.info" in after
    assert "class YOLODetector" in after
    assert "YOLO(model_path)" in after
    assert len(after.splitlines()) > line_count_before
    assert Verifier.verify_file(str(detect_path)) == "PASS"


# ── Test 6: Syntax error → verification fails → rollback → retry ───


@pytest.mark.asyncio
async def test_6_syntax_error_rollback_retry(sample_project):
    auth_path = sample_project / "auth.py"
    original = auth_path.read_text(encoding="utf-8")
    FileEditor.backup(str(auth_path))

    attempts = {"count": 0}

    def bad_edit() -> bool:
        attempts["count"] += 1
        if attempts["count"] < 2:
            auth_path.write_text(original + "\ndef broken(\n", encoding="utf-8")
            return True
        result = FileTool.edit_file(
            str(auth_path),
            '        return "jwt_token"',
            '        return "jwt_token"\n',
        )
        return result["status"] == "success"

    def verify() -> str:
        return Verifier.verify_file(str(auth_path))

    def rollback() -> None:
        FileEditor.rollback(str(auth_path))

    engine = ReflectionEngine(max_retries=3)
    success = await engine.execute_with_reflection_sync(bad_edit, verify, rollback)

    assert success
    assert attempts["count"] >= 2
    assert Verifier.verify_file(str(auth_path)) == "PASS"
    assert auth_path.read_text(encoding="utf-8") == original or "def authenticate" not in auth_path.read_text()


# ── Test 7: create_file refuses to overwrite existing files ──────────


def test_create_file_refuses_overwrite(sample_project):
    existing = sample_project / "auth.py"
    result = FileTool.create_file(str(existing), "OVERWRITE")
    assert result["status"] == "error"
    assert "already exists" in result["message"]
    assert "OVERWRITE" not in existing.read_text(encoding="utf-8")


# ── Test 8: Patch tools registered ──────────────────────────────────


def test_editing_tools_registered():
    from tools.tool_registry import setup_registry

    registry = setup_registry()
    required = {
        "edit_file", "append_file", "insert_before", "insert_after",
        "replace_regex", "delete_block", "rename_symbol", "find_symbol",
        "find_references", "generate_diff", "apply_patch", "validate_patch",
    }
    registered = set(registry.list_tools())
    assert required.issubset(registered)


# ── Test: find_symbol and find_references ───────────────────────────


def test_find_symbol_and_references(sample_project):
    sym = FileTool.find_symbol(str(sample_project), "login")
    assert sym["status"] == "success"
    assert sym["count"] >= 1

    refs = FileTool.find_references(str(sample_project), "login")
    assert refs["status"] == "success"
    assert refs["count"] >= 2


# ── Test: unified diff logging ──────────────────────────────────────


def test_diff_logging(sample_project, tmp_path):
    from editing.diff_logger import DiffLogger

    logger = DiffLogger(diff_dir=tmp_path / "diffs")
    old = "def hello():\n    pass\n"
    new = "def hello():\n    print('hi')\n"
    path = logger.log_edit("test.py", old, new, "test")
    assert Path(path).exists()
    content = Path(path).read_text(encoding="utf-8")
    assert "---" in content
    assert "+++" in content


# ── Test: patch validator rejects bad syntax ────────────────────────


def test_patch_validator_rejects_syntax():
    bad = "def broken(\n"
    valid, _ = PatchValidator.validate_syntax(bad, "test.py")
    assert not valid
    valid, _ = PatchValidator.validate_syntax("def ok():\n    pass\n", "test.py")
    assert valid


def test_tsx_with_apostrophe_passes_validation():
    tsx = (
        "export default function Page() {\n"
        "  return <p>Don't have an account?</p>;\n"
        "}\n"
    )
    valid, _ = PatchValidator.validate_syntax(tsx, "page.tsx")
    assert valid


def test_login_page_tsx_brace_balance():
    from pathlib import Path
    login = Path(r"C:\Users\reyan\OneDrive\Desktop\SkillLens\src\app\login\page.tsx")
    if login.exists():
        content = login.read_text(encoding="utf-8")
        valid, _ = PatchValidator.validate_syntax(content, "page.tsx")
        assert valid


# ── Test 8: FastAPI CRUD workflow structure ─────────────────────────


@pytest.mark.asyncio
async def test_8_fastapi_crud_creates_new_files_only(tmp_path):
    """Build FastAPI CRUD — creates new files, does not overwrite existing."""
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )

    models_content = (
        "from pydantic import BaseModel\n\n"
        "class ItemCreate(BaseModel):\n    name: str\n    price: float\n\n"
        "class ItemResponse(BaseModel):\n    id: int\n    name: str\n    price: float\n"
    )
    router_content = (
        "from fastapi import APIRouter\n"
        "from models import ItemCreate, ItemResponse\n\n"
        "router = APIRouter()\n"
        "_items = []\n"
        "_next_id = 1\n\n"
        "@router.post('/items', response_model=ItemResponse)\n"
        "def create_item(item: ItemCreate):\n"
        "    global _next_id\n"
        "    entry = {'id': _next_id, **item.model_dump()}\n"
        "    _items.append(entry)\n"
        "    _next_id += 1\n"
        "    return entry\n\n"
        "@router.get('/items', response_model=list[ItemResponse])\n"
        "def list_items():\n"
        "    return _items\n"
    )

    for name, content in [("models.py", models_content), ("routers/items.py", router_content)]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        result = FileTool.create_file(str(path), content)
        assert result["status"] == "success"
        assert Verifier.verify_file(str(path)) == "PASS"

    main_path = tmp_path / "main.py"
    original_main = main_path.read_text(encoding="utf-8")
    result = FileTool.create_file(str(main_path), "SHOULD FAIL")
    assert result["status"] == "error"
    assert main_path.read_text(encoding="utf-8") == original_main

    edit_result = FileTool.insert_after(
        str(main_path),
        "app = FastAPI()",
        "from routers.items import router as items_router\napp.include_router(items_router)",
    )
    assert edit_result["status"] == "success"
    assert Verifier.verify_file(str(main_path)) == "PASS"


@pytest.mark.asyncio
async def test_workflow_fails_when_no_patches(sample_project):
    from core.coding_engine import ROLE_REFLECTOR, STATUS_RETRY_EXHAUSTED, CodingLoopResult
    from editing.coding_workflow import CodingWorkflow

    workflow = CodingWorkflow(project_root=sample_project)
    with patch(
        "core.coding_engine.run_coding_loop", new_callable=AsyncMock
    ) as mock_loop:
        mock_loop.return_value = CodingLoopResult(
            status=STATUS_RETRY_EXHAUSTED,
            message="No patches generated.",
            role=ROLE_REFLECTOR,
        )
        result = await workflow.run("Implement quantum flux capacitor module")
        assert not result.success
        assert result.files_modified == []
        assert result.used_coding_loop is True
        mock_loop.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_patch_generation_creates_file(tmp_path):
    from core.coding_engine import ROLE_REFLECTOR, STATUS_SUCCESS, CodingLoopResult
    from editing.coding_workflow import CodingWorkflow

    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )

    async def fake_loop(request, **kwargs):
        (tmp_path / "auth.py").write_text("class Auth:\n    pass\n", encoding="utf-8")
        return CodingLoopResult(
            status=STATUS_SUCCESS,
            message="created auth.py",
            role=ROLE_REFLECTOR,
            changed_paths=["auth.py"],
        )

    workflow = CodingWorkflow(project_root=tmp_path)
    with patch("core.coding_engine.run_coding_loop", new=fake_loop):
        result = await workflow.run("Add authentication module")

    assert result.success
    assert result.used_coding_loop is True
    assert (tmp_path / "auth.py").exists()
    assert "class Auth" in (tmp_path / "auth.py").read_text(encoding="utf-8")
