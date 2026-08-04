from __future__ import annotations

from pathlib import Path


def test_format_single_folder_report_is_live_fs(tmp_path):
    p = tmp_path / "AlgoVerse"
    p.mkdir()
    (p / "data").mkdir()
    (p / "models").mkdir()
    (p / "README.md").write_text("hello\n", encoding="utf-8")
    (p / "run.py").write_text("print('ok')\n", encoding="utf-8")

    from core.desktop_scanner import format_single_folder_report

    report = format_single_folder_report(p)
    assert "AlgoVerse" in report
    assert "data" in report
    assert "models" in report
    assert "README.md" in report
    assert "run.py" in report
    # Must not hallucinate generic YOLO folders if absent
    assert "labels" not in report
    assert "images" not in report


def test_resolve_scan_target_by_number_and_name(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    projects = desktop / "Projects"
    desktop.mkdir()
    projects.mkdir()
    (desktop / "Alpha").mkdir()
    (desktop / "AlgoVerse").mkdir()
    (projects / "stocks_app").mkdir()
    (projects / "SkillLens").mkdir()

    monkeypatch.setattr("core.desktop_scanner.get_desktop_path", lambda: desktop)

    from core.desktop_scanner import resolve_scan_target

    by_name = resolve_scan_target("analyze algoverse folder on desktop")
    assert by_name is not None
    assert by_name.name == "AlgoVerse"

    by_num = resolve_scan_target("scan number 2 algoverse on desktop")
    assert by_num is not None
    assert by_num.name == "AlgoVerse"


def test_hud_scan_uses_deterministic_folder_report(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    projects = desktop / "Projects"
    desktop.mkdir()
    projects.mkdir()
    algo = desktop / "AlgoVerse"
    algo.mkdir()
    (algo / "notes.txt").write_text("n\n", encoding="utf-8")
    (algo / "src").mkdir()

    monkeypatch.setattr("core.desktop_scanner.get_desktop_path", lambda: desktop)

    from tools.hud_agent import handle_hud_request

    out = handle_hud_request("now scan algoverse folder on desktop")
    assert "Folder: `AlgoVerse`" in out
    assert "notes.txt" in out
    assert "src" in out


def test_hud_open_explicit_path_not_fuzzy(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "AlgoVerse"
    target.mkdir()
    other = desktop / "Dense-Object-Detection-main"
    other.mkdir()

    monkeypatch.setattr("core.desktop_scanner.get_desktop_path", lambda: desktop)
    launched: list[str] = []

    def _fake_popen(cmd, *args, **kwargs):
        launched.append(str(cmd))
        class _P:
            pass
        return _P()

    monkeypatch.setattr("shutil.which", lambda _x: "code")
    monkeypatch.setattr("subprocess.Popen", _fake_popen)

    from tools.hud_agent import handle_hud_request

    out = handle_hud_request(f"open {target}")
    assert "AlgoVerse" in out
    assert "Dense-Object-Detection-main" not in out
    assert any("AlgoVerse" in x for x in launched)
