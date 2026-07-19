"""Phase 4 self-debug tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from editing.error_classifier import (
    ErrorKind,
    classify_error,
    extract_missing_module,
    is_error_report,
)
from editing.self_debug import SelfDebugOrchestrator


def test_is_error_report_detects_stack_trace():
    text = "Error Type: RuntimeError\nError Message: failed\nTraceback:\n  at foo"
    assert is_error_report(text) is True


def test_classify_rsc_manifest():
    text = "Could not find the module in the React Client Manifest file is empty"
    assert classify_error(text) == ErrorKind.RSC_MANIFEST


def test_classify_home_404():
    assert classify_error("GET / 404 in 12ms") == ErrorKind.HOME_404


def test_extract_missing_module():
    text = "Module not found: Can't resolve '@/components/ui/label'"
    assert extract_missing_module(text) == "components/ui/label"


def test_corruption_patches_clean_layout(tmp_path):
    layout = tmp_path / "src" / "app" / "layout.tsx"
    layout.parent.mkdir(parents=True)
    layout.write_text(
        '"use client"\nexport default function RootLayout() { const x = useAuth(); }\n',
        encoding="utf-8",
    )
    patches = SelfDebugOrchestrator.corruption_patches(tmp_path)
    assert len(patches) >= 1
    assert any("layout.tsx" in p.path for p in patches)


@pytest.mark.asyncio
async def test_self_debug_run_from_error_rsc(tmp_path):
    layout = tmp_path / "src" / "app" / "layout.tsx"
    layout.parent.mkdir(parents=True)
    layout.write_text(
        '"use client"\nimport { useState } from "react"\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"next": "15.0.0", "react": "19.0.0"}}',
        encoding="utf-8",
    )

    with patch("editing.coding_workflow.Verifier.verify_nextjs_build", return_value=(True, "skip")):
        with patch.object(
            SelfDebugOrchestrator,
            "build_fix_patches",
            return_value=[],
        ):
            from editing.coding_workflow import CodingWorkflow

            with patch.object(
                CodingWorkflow,
                "run",
                new_callable=AsyncMock,
                return_value=type("R", (), {"success": True, "message": "ok", "files_modified": []})(),
            ):
                result = await SelfDebugOrchestrator.run_from_error(
                    "React Client Manifest could not find the module",
                    tmp_path,
                )
    assert result.success is True


def test_build_fix_patches_home_404(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    patches = SelfDebugOrchestrator.build_fix_patches("GET / 404", tmp_path)
    assert any("page.tsx" in p.path for p in patches)
