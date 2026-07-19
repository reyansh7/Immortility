"""Tests for path normalization and unified diff application."""

from pathlib import Path

from core.paths import FileContentIndex, normalize_path_key, to_rel_path
from editing.unified_diff import apply_unified_diff
from tools.file_tool import FileTool


def test_file_content_index_absolute_vs_relative(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    auth = root / "auth.py"
    auth.write_text("def login():\n    pass\n", encoding="utf-8")

    index = FileContentIndex(root)
    index.add(str(auth.resolve()), auth.read_text(encoding="utf-8"))

    assert index.get("auth.py") is not None
    assert index.get(str(auth.resolve())) is not None
    assert "def login" in index.get("auth.py")


def test_normalize_path_key_relative_to_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    rel = "src/app.py"
    abs_path = normalize_path_key(rel, root)
    assert Path(abs_path) == (root / "src" / "app.py").resolve()


def test_to_rel_path(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "src" / "main.py"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    assert to_rel_path(f, root) == "src/main.py"


def test_apply_unified_diff_inserts_line():
    original = "line1\nline2\nline3\n"
    patch = (
        "--- a/file\n+++ b/file\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "+inserted\n"
        " line2\n"
        " line3\n"
    )
    result = apply_unified_diff(original, patch)
    assert result is not None
    assert "inserted" in result
    assert result.count("\n") == original.count("\n") + 1


def test_replace_lines_rejects_out_of_range(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    result = FileTool.replace_lines(str(f), 1, 10, "x")
    assert result["status"] == "error"


def test_normalize_edit_file_content_to_replacement_text():
    from editing.patch_args import normalize_patch_args

    op, args = normalize_patch_args("edit_file", {
        "target_text": "</motion.div>",
        "content": "</motion.div>",
    })
    assert op == "edit_file"
    assert args["replacement_text"] == "</motion.div>"
    assert "content" not in args


def test_normalize_edit_file_full_content_becomes_write_file():
    from editing.patch_args import normalize_patch_args

    op, args = normalize_patch_args("edit_file", {"content": "export default function Page() {}"})
    assert op == "write_file"
    assert args["content"] == "export default function Page() {}"


def test_coding_workflow_applies_edit_file_with_content_alias(tmp_path):
    from editing.coding_workflow import CodingWorkflow
    from editing.patch_generator import PatchOperation

    f = tmp_path / "page.tsx"
    f.write_text("<div>broken</div>\n", encoding="utf-8")

    workflow = CodingWorkflow(project_root=tmp_path)
    patch = PatchOperation(
        path=str(f),
        operation="edit_file",
        args={"target_text": "<div>broken</div>", "content": "<div>fixed</div>"},
        reason="fix tag",
    )
    result = workflow._execute_patch(patch)
    assert result["status"] == "success"
    assert "<div>fixed</div>" in f.read_text(encoding="utf-8")
