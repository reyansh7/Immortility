"""PROJECT CodingWorkflow adapter uses the canonical loop for general coding."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.coding_engine import ROLE_REFLECTOR, STATUS_SUCCESS, CodingLoopResult
from editing.coding_workflow import CodingWorkflow, EditResult


@pytest.mark.asyncio
async def test_general_coding_uses_canonical_loop(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    workflow = CodingWorkflow(project_root=tmp_path)

    with patch("core.coding_engine.run_coding_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = CodingLoopResult(
            status=STATUS_SUCCESS,
            message="done",
            role=ROLE_REFLECTOR,
            changed_paths=["app.py"],
        )
        result = await workflow.run("Implement a helper module for widgets")

    assert isinstance(result, EditResult)
    assert result.success
    assert result.used_coding_loop is True
    assert result.files_modified == ["app.py"]
    mock_loop.assert_awaited_once()
