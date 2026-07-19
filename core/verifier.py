"""Project-level verifier for Phase 3 workflows."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from editing.verifier import Verifier as FileVerifier

logger = logging.getLogger(__name__)


class Verifier:
    """Verify modified files, Next.js build, and optional pytest."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    async def verify_all(self, files: list[str] | None = None) -> dict[str, Any]:
        targets = files or []
        if not targets:
            return {"success": True, "details": "No files to verify", "results": {}}

        results: dict[str, str] = {}
        failures: list[str] = []
        for fpath in targets:
            path = Path(fpath)
            if not path.is_absolute():
                path = self.project_root / path
            status = FileVerifier.verify_file(str(path), self.project_root)
            results[str(path)] = status
            if status != "PASS":
                failures.append(f"{path.name}: {status}")

        success = len(failures) == 0
        details = "All files passed" if success else "; ".join(failures)
        return {"success": success, "details": details, "results": results}

    async def verify_project(self, files: list[str] | None = None) -> dict[str, Any]:
        """Full CI completion gate: lint → typecheck → tests (plus optional build)."""
        from editing.ci_gate import CIGate

        file_result = await self.verify_all(files)
        if not file_result["success"]:
            file_result["stage"] = "file"
            return file_result

        # Targeted syntax already covered; completion uses full CI
        ci = CIGate(self.project_root)
        ci_result = ci.run_full_pipeline(files or [])
        if not ci_result.get("success"):
            return {
                "success": False,
                "details": ci_result.get("details", "CI failed"),
                "stage": ci_result.get("stage", "ci"),
                "stages": ci_result.get("stages", []),
                "results": file_result.get("results", {}),
            }

        ok, build_msg = FileVerifier.verify_nextjs_build(self.project_root)
        if not ok:
            return {
                "success": False,
                "details": build_msg,
                "stage": "build",
                "build_output": build_msg,
                "results": file_result.get("results", {}),
            }

        return {
            "success": True,
            "details": "files + CI pipeline + build passed",
            "results": file_result.get("results", {}),
            "ci": ci_result,
            "build": build_msg,
        }
