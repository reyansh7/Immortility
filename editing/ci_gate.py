"""Full CI-style verification gate for task completion."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from core.repo_detector import RepositoryDetector
from editing.patch_validator import PatchValidator

logger = logging.getLogger(__name__)


class CIGate:
    """Lint → typecheck → full test suite. Used only at task completion."""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()

    def run_full_pipeline(self, files: list[str] | None = None) -> dict[str, Any]:
        stages: list[dict[str, Any]] = []

        lint = self._lint(files or [])
        stages.append(lint)
        if not lint["ok"]:
            return self._fail("lint", stages, lint["detail"])

        types = self._typecheck(files or [])
        stages.append(types)
        if not types["ok"]:
            return self._fail("typecheck", stages, types["detail"])

        tests = self._full_tests()
        stages.append(tests)
        if not tests["ok"]:
            return self._fail("tests", stages, tests["detail"])

        return {
            "success": True,
            "stage": "complete",
            "details": "CI pipeline passed (lint → typecheck → tests)",
            "stages": stages,
        }

    def run_targeted(self, files: list[str]) -> dict[str, Any]:
        """Fast path during iterative edits: syntax-only on touched files."""
        failures: list[str] = []
        for f in files:
            path = Path(f)
            if not path.is_absolute():
                path = self.root / path
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            ok, msg = PatchValidator.validate_syntax(content, str(path))
            if not ok:
                failures.append(f"{path.name}: {msg}")
        if failures:
            return {
                "success": False,
                "stage": "targeted",
                "details": "; ".join(failures),
            }
        return {"success": True, "stage": "targeted", "details": "targeted syntax ok"}

    # ── Stages ──────────────────────────────────────────────────────

    def _lint(self, files: list[str]) -> dict[str, Any]:
        py_files = [f for f in files if str(f).endswith(".py")]
        ts_files = [f for f in files if str(f).endswith((".ts", ".tsx", ".js", ".jsx"))]

        # Always do deterministic syntax checks first
        for f in files:
            path = Path(f)
            if not path.is_absolute():
                path = self.root / path
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            ok, msg = PatchValidator.validate_syntax(content, str(path))
            if not ok:
                return {"ok": False, "name": "lint", "detail": f"{path.name}: {msg}"}

        profile = RepositoryDetector.detect(self.root)
        if profile.kind == "python" and py_files:
            # py_compile each file
            for f in py_files:
                path = Path(f) if Path(f).is_absolute() else self.root / f
                res = subprocess.run(
                    ["python", "-m", "py_compile", str(path)],
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if res.returncode != 0:
                    return {
                        "ok": False,
                        "name": "lint",
                        "detail": (res.stderr or res.stdout or "py_compile failed")[-2000:],
                    }
            # optional flake8
            res = subprocess.run(
                ["flake8", *[str(Path(f) if Path(f).is_absolute() else self.root / f) for f in py_files[:20]]],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if res.returncode != 0 and res.stdout.strip():
                if any(x in res.stdout for x in ("SyntaxError", "IndentationError", "E999")):
                    return {"ok": False, "name": "lint", "detail": res.stdout[-2000:]}

        if profile.kind == "node" and ts_files:
            res = subprocess.run(
                ["npx", "eslint", *[str(f) for f in ts_files[:20]]],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            out = (res.stdout or "") + (res.stderr or "")
            if res.returncode != 0 and ("Parsing error" in out or "Syntax error" in out):
                return {"ok": False, "name": "lint", "detail": out[-2000:]}

        return {"ok": True, "name": "lint", "detail": "lint passed/skipped"}

    def _typecheck(self, files: list[str]) -> dict[str, Any]:
        profile = RepositoryDetector.detect(self.root)
        if profile.kind == "python":
            # mypy optional
            res = subprocess.run(
                ["mypy", ".", "--ignore-missing-imports", "--no-error-summary"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            # If mypy missing, skip
            if "not recognized" in (res.stderr or "").lower() or res.returncode == 127:
                return {"ok": True, "name": "typecheck", "detail": "skip: mypy unavailable"}
            # Only fail hard on syntax-ish errors; treat clean exit as pass
            if res.returncode == 0:
                return {"ok": True, "name": "typecheck", "detail": "mypy passed"}
            # Soft-skip when mypy not installed as module
            if "No module named" in (res.stderr or "") or "not found" in (res.stderr or "").lower():
                return {"ok": True, "name": "typecheck", "detail": "skip: mypy unavailable"}
            # Presence of error: lines — fail
            out = (res.stdout or "") + (res.stderr or "")
            if "error:" in out.lower():
                return {"ok": False, "name": "typecheck", "detail": out[-2500:]}
            return {"ok": True, "name": "typecheck", "detail": "typecheck soft-pass"}

        if profile.kind == "node":
            # Prefer tsc --noEmit when tsconfig exists
            if (self.root / "tsconfig.json").exists():
                res = subprocess.run(
                    ["npx", "tsc", "--noEmit"],
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                out = (res.stdout or "") + (res.stderr or "")
                if res.returncode != 0 and "error TS" in out:
                    return {"ok": False, "name": "typecheck", "detail": out[-2500:]}
                if "could not determine executable" in out.lower() or "not found" in out.lower():
                    return {"ok": True, "name": "typecheck", "detail": "skip: tsc unavailable"}
            return {"ok": True, "name": "typecheck", "detail": "typecheck passed/skipped"}

        return {"ok": True, "name": "typecheck", "detail": f"skip: kind={profile.kind}"}

    def _full_tests(self) -> dict[str, Any]:
        from editing.verifier import Verifier

        ok, msg = Verifier.verify_project_tests(self.root)
        return {"ok": ok, "name": "tests", "detail": msg}

    @staticmethod
    def _fail(stage: str, stages: list[dict[str, Any]], detail: str) -> dict[str, Any]:
        return {
            "success": False,
            "stage": stage,
            "details": detail,
            "stages": stages,
        }
