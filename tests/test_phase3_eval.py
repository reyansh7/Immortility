"""Phase 3 mocked eval suite."""

from __future__ import annotations

import pytest

from evaluation.phase3 import run_phase3_eval


@pytest.mark.asyncio
async def test_phase3_eval_all_mocked_cases_pass():
    report = await run_phase3_eval()
    failed = [c["id"] for c in report["cases"] if not c["ok"]]
    assert report["ok"], f"failed cases: {failed}"
    assert report["total"] >= 12
    assert all(c["traced"] or c["status"] == "cancelled" for c in report["cases"])
