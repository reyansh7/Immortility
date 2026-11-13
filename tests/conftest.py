"""Test isolation: Immortility file tools stay inside tmp + repo + Desktop."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _immortility_fs_sandbox(tmp_path, monkeypatch):
    from core.fs_sandbox import reset_fs_sandbox
    from core.paths import get_desktop_path
    from core.repo_paths import get_repo_root

    roots = os.pathsep.join(
        [
            str(tmp_path.resolve()),
            str(get_repo_root()),
            str(get_desktop_path()),
        ]
    )
    monkeypatch.setenv("IMMORTILITY_FS_ROOTS", roots)
    reset_fs_sandbox()
    yield
    reset_fs_sandbox()
