"""Filesystem sandbox: Immortility cannot leave a temp root."""

from __future__ import annotations

import os
from pathlib import Path

from core.fs_sandbox import allowed_roots, reset_fs_sandbox, scan_storage
from tools.command_tool import CommandTool
from tools.file_tool import FileTool


def _lock_roots(monkeypatch, *roots: Path) -> None:
    monkeypatch.setenv(
        "IMMORTILITY_FS_ROOTS",
        os.pathsep.join(str(r.resolve()) for r in roots),
    )
    reset_fs_sandbox()


def test_default_roots_are_not_the_whole_home(monkeypatch):
    monkeypatch.delenv("IMMORTILITY_FS_ROOTS", raising=False)
    reset_fs_sandbox()
    home = Path.home().resolve()
    roots = [p.resolve() for p in allowed_roots()]
    assert home not in roots
    assert all(p != home for p in roots)


def test_read_write_inside_sandbox(tmp_path, monkeypatch):
    box = tmp_path / "box"
    box.mkdir()
    _lock_roots(monkeypatch, box)
    target = box / "note.txt"
    created = FileTool.create_file(str(target), "hello")
    assert created["status"] == "success"
    listed = FileTool.list_directory(str(box))
    assert "note.txt" in listed["files"]
    read = FileTool.read_file(str(target))
    assert read["content"] == "hello"


def test_cannot_read_or_delete_outside_sandbox(tmp_path, monkeypatch):
    box = tmp_path / "box"
    outside = tmp_path / "outside"
    box.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("do-not-touch", encoding="utf-8")
    _lock_roots(monkeypatch, box)

    assert FileTool.read_file(str(secret))["status"] == "error"
    assert FileTool.list_directory(str(outside))["status"] == "error"
    assert FileTool.delete_file(str(secret))["status"] == "error"
    assert FileTool.write_file(str(outside / "pwn.txt"), "x")["status"] == "error"
    assert secret.read_text(encoding="utf-8") == "do-not-touch"
    assert not (outside / "pwn.txt").exists()


def test_parent_traversal_is_blocked(tmp_path, monkeypatch):
    box = tmp_path / "box"
    box.mkdir()
    (box / "ok.txt").write_text("in", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("out", encoding="utf-8")
    _lock_roots(monkeypatch, box)

    escaped = FileTool.read_file(str(box / ".." / "outside.txt"))
    assert escaped["status"] == "error"
    assert "out" not in str(escaped.get("content", ""))


def test_sensitive_names_blocked_even_inside_sandbox(tmp_path, monkeypatch):
    box = tmp_path / "box"
    ssh = box / ".ssh"
    ssh.mkdir(parents=True)
    key = ssh / "id_rsa"
    key.write_text("PRIVATE", encoding="utf-8")
    env_file = box / ".env"
    env_file.write_text("NVIDIA_API_KEY=secret", encoding="utf-8")
    _lock_roots(monkeypatch, box)

    assert FileTool.read_file(str(key))["status"] == "error"
    assert FileTool.read_file(str(env_file))["status"] == "error"
    assert FileTool.list_directory(str(ssh))["status"] == "error"


def test_storage_scan_stays_inside_sandbox(tmp_path, monkeypatch):
    box = tmp_path / "box"
    junk = box / "junk"
    keep = box / "keep"
    junk.mkdir(parents=True)
    keep.mkdir()
    (keep / "small.txt").write_text("tiny", encoding="utf-8")
    (junk / "big.bin").write_bytes(b"x" * 50_000)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge.bin").write_bytes(b"y" * 200_000)
    _lock_roots(monkeypatch, box)

    report = scan_storage(str(box), limit=10)
    assert report["status"] == "success"
    paths = " ".join(row["path"] for row in report["largest_files"])
    assert "big.bin" in paths
    assert "huge.bin" not in paths
    assert report["total_bytes"] == 50_000 + 4  # "tiny"

    escaped = scan_storage(str(outside), limit=10)
    assert escaped["status"] == "error"


def test_find_files_does_not_default_to_home(tmp_path, monkeypatch):
    box = tmp_path / "box"
    box.mkdir()
    (box / "hit.txt").write_text("x", encoding="utf-8")
    _lock_roots(monkeypatch, box)
    found = FileTool.find_files(pattern="hit.txt")
    assert found["status"] == "success"
    assert found["count"] == 1
    assert Path(found["root"]).resolve() == box.resolve()


def test_home_secrets_stay_blocked_if_roots_widen_later(monkeypatch):
    home = Path.home()
    _lock_roots(monkeypatch, home)
    ssh = home / ".ssh" / "id_rsa"
    roaming = home / "AppData" / "Roaming"
    assert FileTool.read_file(str(ssh))["status"] == "error"
    assert FileTool.list_directory(str(roaming))["status"] == "error"


def test_command_cwd_outside_sandbox_is_blocked(tmp_path, monkeypatch):
    box = tmp_path / "box"
    outside = tmp_path / "outside"
    box.mkdir()
    outside.mkdir()
    _lock_roots(monkeypatch, box)
    result = CommandTool.run_command("echo hi", cwd=str(outside))
    assert result["status"] == "error"
    assert result.get("error_code") == "SANDBOX_CWD"
