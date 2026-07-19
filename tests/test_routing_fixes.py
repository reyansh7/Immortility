"""Tests for routing helpers and confirmation flow."""

from core.project_extract import extract_open_path, is_implementation_confirm
from core.pending_action import is_approval
from core.text_sanitize import sanitize_text


def test_extract_open_path_mid_message(tmp_path):
    proj = tmp_path / "SkillLens"
    proj.mkdir()
    msg = f"debug 404 /open {proj}"
    cleaned, path = extract_open_path(msg)
    assert "SkillLens" in (path or "")
    assert "/open" not in cleaned


def test_is_implementation_confirm():
    assert is_implementation_confirm("yes make the changes")
    assert is_implementation_confirm("make these changes")
    assert not is_implementation_confirm("what is jwt")


def test_is_approval_prefix():
    assert is_approval("yes implement it")
    assert is_approval("yes")


def test_sanitize_surrogates():
    bad = "hello\ud800world"
    assert "\ud800" not in sanitize_text(bad)


def test_home_404_scaffold(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    from editing.coding_workflow import CodingWorkflow
    wf = CodingWorkflow(project_root=tmp_path)
    patches = wf._build_missing_home_page_patch()
    assert len(patches) == 1
    assert patches[0].path.endswith("page.tsx")


def test_run_project_request():
    from core.project_extract import is_run_project_request
    assert is_run_project_request("run the project SkillLens")
    assert is_run_project_request("start frontend and backend")
    assert not is_run_project_request("implement jwt authentication")


def test_open_path_only_stops_at_extra_words():
    from core.project_extract import extract_open_path_only
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        skill = Path(tmp) / "SkillLens"
        skill.mkdir()
        line = f"/open {skill} implement jwt auth"
        path = extract_open_path_only(line)
        assert path == str(skill.resolve())
    from editing.coding_workflow import CodingWorkflow
    wf = CodingWorkflow()
    assert not wf._is_runtime_error_report("GET / 404 in 419ms")
    assert wf._is_runtime_error_report("Could not find the module in the React Client Manifest")
