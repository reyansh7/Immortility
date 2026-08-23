"""Fresh-context reviewer is read-only and never reuses coder conversation."""

from __future__ import annotations

from core.coding_engine import CheckResult, ReviewResult, infer_plan
from core.coding_reviewer import (
    git_review_packet,
    review_coding_result,
    review_from_llm_payload,
    try_llm_review,
)


def test_review_from_llm_payload_maps_verdicts():
    packet = ReviewResult(ok=True, summary="changed=1", changed_paths=["a.py"], patch="diff")
    parsed = review_from_llm_payload(
        {"verdict": "NEEDS_CHANGES", "summary": "add tests", "findings": ["missing test"]},
        packet,
    )
    assert parsed is not None
    assert not parsed.ok
    assert parsed.verdict == "NEEDS_CHANGES"
    assert parsed.changed_paths == ["a.py"]
    assert parsed.patch == "diff"


def test_try_llm_review_skips_when_cancelled(tmp_path, monkeypatch):
    class FakeKernel:
        def cancelled(self):
            return True

        def run_model(self, *_a, **_k):
            raise AssertionError("fresh review must not call the model when cancelled")

    monkeypatch.setattr("core.execution_kernel.get_kernel", lambda: FakeKernel())
    monkeypatch.setattr(
        "core.coding_reviewer.git_review_packet",
        lambda _cwd: ReviewResult(ok=True, summary="ok", changed_paths=["a.py"]),
    )
    plan = infer_plan("fix a.py")
    assert try_llm_review(tmp_path, plan, CheckResult(ok=True, output="pass")) is None


def test_review_coding_result_falls_back_to_git_packet(tmp_path, monkeypatch):
    packet = ReviewResult(ok=True, summary="branch=main changed=1", changed_paths=["a.py"])
    monkeypatch.setattr("core.coding_reviewer.git_review_packet", lambda _cwd: packet)
    monkeypatch.setattr("core.coding_reviewer.try_llm_review", lambda *_a, **_k: None)
    result = review_coding_result(tmp_path, use_llm=True)
    assert result.summary == packet.summary
    assert result.changed_paths == ["a.py"]


def test_git_review_packet_is_read_only(monkeypatch, tmp_path):
    seen = []

    class FakeGit:
        @staticmethod
        def status(cwd=None):
            seen.append("status")
            return {"status": "success", "branch": "main", "files": [{"path": "a.py"}]}

        @staticmethod
        def diff(cwd=None):
            seen.append("diff")
            return {"patch": "diff --git a/a.py", "files": ["a.py"]}

        @staticmethod
        def reset(*_a, **_k):
            raise AssertionError("reviewer must not reset")

    monkeypatch.setattr("tools.git_tool.GitTool", FakeGit)
    packet = git_review_packet(tmp_path)
    assert seen == ["status", "diff"]
    assert packet.changed_paths == ["a.py"]
    assert packet.patch.startswith("diff")
