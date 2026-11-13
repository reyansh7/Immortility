"""Live Desktop/Projects scanner — no LLM required."""

from core.desktop_scanner import (
    build_projects_fact_block,
    list_project_folders,
    projects_dir,
    wants_projects_scan,
)


def test_projects_dir_exists_and_scan_matches_filesystem():
    root = projects_dir()
    assert root.is_dir(), f"missing Projects dir: {root}"
    folders = list_project_folders(root)
    assert folders, f"expected at least one folder in {root}"
    # The scan must be ground truth, not a cached/hardcoded list
    on_disk = {p.name for p in root.iterdir() if p.is_dir()}
    assert set(folders) <= on_disk
    assert folders == sorted(folders, key=str.lower)


def test_fact_block_lists_all_and_forbids_invention():
    block = build_projects_fact_block(include_desktop=True, deep=False)
    folders = list_project_folders()
    assert f"Count of folders inside Projects: {len(folders)}" in block
    for name in folders:
        assert name in block
    assert "authoritative" in block.lower()
    assert "do NOT invent" in block or "do not invent" in block.lower()
    # Folder names from RAG hallucinations must not appear as listed projects
    listed = block.split("Folders inside Desktop/Projects")[1].split("Also on Desktop")[0]
    assert "otTables" not in listed
    assert "varLib" not in listed


def test_wants_projects_scan_phrases():
    assert wants_projects_scan("list all the folders in the projects folder")
    assert wants_projects_scan("Analyze the project folder on desktop")
    assert wants_projects_scan("analyze desktop/projects")
    assert wants_projects_scan("analyze all the projects in my desktop")
    assert wants_projects_scan("There are many other folders not just three")
    assert wants_projects_scan("what projects am I working on")
    assert wants_projects_scan("scan my pc")
    assert wants_projects_scan("scan pc")
    assert wants_projects_scan("pc scan")
    assert wants_projects_scan("scan my computer")
    assert wants_projects_scan("scan system")
    assert wants_projects_scan("scan my desktop")
    assert wants_projects_scan("scan machine")
    assert wants_projects_scan("scan projects")
    assert not wants_projects_scan("fix the login bug in auth.py")
    # Must NOT steal coding / LeetCode / write-file intents
    assert not wants_projects_scan("write it in python and create a new file in desktop")
    assert not wants_projects_scan("3664. Two-Letter Card Game solve this leetcode")
    assert not wants_projects_scan("save the solution to desktop as a python file")
    # Index / deep-read must NOT become a listing
    assert not wants_projects_scan("index projects in my desktop yourself and read")
    assert not wants_projects_scan("read the projects")
    assert not wants_projects_scan(r"/open C:\Users\reyan\OneDrive\Desktop\Projects")
    assert not wants_projects_scan("ingest all projects on desktop")


def test_answer_scan_my_pc_returns_live_report():
    from core.desktop_scanner import answer_desktop_projects_query

    reply = answer_desktop_projects_query("scan my pc", learn=False)
    assert "# Desktop/Projects — live scan" in reply
    assert "**Folder count:**" in reply
    assert "## Folders inside Desktop/Projects" in reply
    assert "Also on Desktop" in reply


def test_resolve_scan_target_explicit_windows_path():
    from pathlib import Path
    from core.desktop_scanner import format_single_folder_report, resolve_scan_target

    target = resolve_scan_target("scan my  C:\\Users\\reyan")
    assert target is not None
    assert target == Path("C:\\Users\\reyan").resolve()

    report = format_single_folder_report(target)
    assert "Path: `C:\\Users\\reyan`" in report
    assert "Top-level folders:" in report


def test_hud_scan_explicit_path():
    from tools.hud_agent import handle_hud_request

    reply = handle_hud_request("scan my  C:\\Users\\reyan")
    assert "Path: `C:\\Users\\reyan`" in reply
    assert "Top-level folders:" in reply


def test_format_projects_report_lists_all():
    from core.desktop_scanner import format_projects_report

    report = format_projects_report(deep=False, include_desktop=False)
    folders = list_project_folders()
    assert f"**Folder count:** {len(folders)}" in report
    for name in folders:
        assert name in report
    assert "Resume Analyzer" not in report or "Resume_Analyzer" in report


def test_answer_existence_query_is_concise():
    from core.desktop_scanner import answer_desktop_projects_query

    reply = answer_desktop_projects_query(
        "analyze desktop and tell me if any projects are there",
        learn=False,
    )
    assert "Desktop/Projects — live scan" not in reply
    assert "dirs:" not in reply.lower()
    low = reply.lower()
    assert "yes" in low or "project" in low or "folder" in low
    # Should not dump every file tree
    assert "node_modules" not in low
    assert len(reply) < 900


def test_explain_project_from_disk_and_followup():
    from core.desktop_scanner import explain_project_from_disk, wants_project_explanation
    from tools.hud_agent import handle_hud_request

    assert wants_project_explanation("explain what the project is")
    assert wants_project_explanation("what is LedgerOS")
    assert wants_project_explanation("explain the codebase")

    # Scan LedgerOS first
    scan_res = handle_hud_request("scan LedgerOS")
    assert "LedgerOS" in scan_res

    # Follow up asking to explain what the project is
    exp_res = handle_hud_request("explain what the project is")
    assert "LedgerOS" in exp_res
    assert "Financial Intelligence" in exp_res or "Path:" in exp_res
