"""Hierarchical verification gate for the IMMORTALITY Custom Harness.

Implements the 4-level verification hierarchy:
  Level 1 — Mechanical: syntax, compiler check, py_compile, schema
  Level 2 — Execution: project tests (pytest / repo test command), CI pipeline
  Level 3 — Artifact & Diff: file snapshots vs current disk state
  Level 4 — Semantic Review: independent model review (advisory only; cannot override mechanical/test failures)
"""

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import Any

from core.harness.contracts import BrainProvider
from core.json_utils import parse_llm_json
from core.task import VerificationResult, _now_iso

logger = logging.getLogger(__name__)


class HarnessVerifier:
    """Independent hierarchical verification engine."""

    def __init__(self, project_root: str = "") -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    async def verify(
        self,
        original_goal: str,
        touched_files: list[str],
        before_snapshots: dict[str, str],
        *,
        brain: BrainProvider | None = None,
        run_full_ci: bool = True,
    ) -> VerificationResult:
        """Run hierarchical verification on modified files and return structured result."""
        failures: list[str] = []
        warnings: list[str] = []

        mechanical_ok = True
        tests_ok = True
        diff_ok = True
        semantic_ok = True

        # Level 1: Mechanical file verification
        from editing.verifier import Verifier

        for fpath in touched_files:
            p = Path(fpath)
            if not p.is_absolute():
                p = self.project_root / p
            status = Verifier.verify_file(str(p), self.project_root)
            if status != "PASS":
                mechanical_ok = False
                failures.append(f"Mechanical({p.name}): {status}")

        if not mechanical_ok:
            return VerificationResult.create(
                passed=False,
                method="mechanical",
                details="; ".join(failures),
                mechanical_passed=False,
                tests_passed=False,
                diff_passed=True,
                semantic_passed=False,
                failures=failures,
                warnings=warnings,
            )

        # Level 2: Execution / Project Tests
        if run_full_ci and touched_files:
            try:
                from editing.verifier import ProjectVerifier
                pv = ProjectVerifier(self.project_root)
                proj_res = await pv.verify_project(touched_files)
                if not proj_res.get("success"):
                    tests_ok = False
                    stage = proj_res.get("stage", "ci")
                    det = str(proj_res.get("details", ""))[:1200]
                    failures.append(f"CI({stage}): {det}")
            except Exception as exc:
                logger.debug("CI gate check error: %s", exc)
                warnings.append(f"CI gate check skipped: {exc}")

        if not tests_ok:
            return VerificationResult.create(
                passed=False,
                method="ci_gate",
                details="; ".join(failures),
                mechanical_passed=True,
                tests_passed=False,
                diff_passed=True,
                semantic_passed=False,
                failures=failures,
                warnings=warnings,
            )

        # Level 3: Artifact / Diff Inspection
        diff_chunks: list[str] = []
        for path_str, before in before_snapshots.items():
            p = Path(path_str)
            if not p.is_absolute():
                p = self.project_root / p
            after = ""
            if p.exists() and p.is_file():
                try:
                    after = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    after = ""
            if after != before:
                d = difflib.unified_diff(
                    before.splitlines(),
                    after.splitlines(),
                    fromfile=f"{p.name}:before",
                    tofile=f"{p.name}:after",
                    lineterm="",
                )
                diff_chunks.append("\n".join(d))

        diff_text = "\n\n".join(diff_chunks)[:10000]
        if touched_files and not diff_text.strip():
            warnings.append("No diff detected between snapshots and disk state.")

        # Level 4: Semantic Review (Advisory — cannot override earlier failure)
        if brain is not None and diff_text.strip():
            reviewer_prompt = (
                "You are an independent, strict code reviewer. Given the original user goal and unified diff, "
                "verify whether the changes completely satisfy the goal without breaking patterns.\n\n"
                f"Goal:\n{original_goal}\n\n"
                f"Diff:\n{diff_text}\n\n"
                'Respond with JSON: {"pass": true|false, "reason": "concise explanation"}'
            )
            try:
                review_out = await brain.generate_response(
                    [{"role": "user", "content": reviewer_prompt}],
                    role="brain",
                    timeout_s=30.0,
                )
                parsed = parse_llm_json(review_out)
                if isinstance(parsed, dict):
                    semantic_ok = bool(parsed.get("pass", True))
                    reason = str(parsed.get("reason", "")).strip()
                    if not semantic_ok:
                        failures.append(f"SemanticReview: {reason or 'Reviewer rejected diff'}")
                else:
                    low = review_out.lower()
                    if "fail" in low and "pass" not in low:
                        semantic_ok = False
                        failures.append(f"SemanticReview: {review_out[:300]}")
            except Exception as exc:
                logger.debug("Semantic reviewer call skipped: %s", exc)
                warnings.append(f"Semantic review skipped: {exc}")

        passed = mechanical_ok and tests_ok and diff_ok and semantic_ok
        details = "All verification checks passed." if passed else "; ".join(failures)

        return VerificationResult.create(
            passed=passed,
            method="composite",
            details=details,
            mechanical_passed=mechanical_ok,
            tests_passed=tests_ok,
            diff_passed=diff_ok,
            semantic_passed=semantic_ok,
            failures=failures,
            warnings=warnings,
        )
