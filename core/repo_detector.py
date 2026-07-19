"""Detect repository type and map verification / test commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RepoKind = Literal["python", "node", "rust", "go", "unknown"]


@dataclass(frozen=True)
class RepoProfile:
    kind: RepoKind
    root: Path
    test_command: list[str]
    reason: str


class RepositoryDetector:
    """Inspect workspace root manifests and choose test commands."""

    @staticmethod
    def detect(project_root: str | Path) -> RepoProfile:
        root = Path(project_root).resolve()

        # Rust
        if (root / "Cargo.toml").exists():
            return RepoProfile(
                kind="rust",
                root=root,
                test_command=["cargo", "test"],
                reason="Cargo.toml",
            )

        # Go
        if (root / "go.mod").exists():
            return RepoProfile(
                kind="go",
                root=root,
                test_command=["go", "test", "./..."],
                reason="go.mod",
            )

        # Node / JS / TS (prefer package.json test script)
        pkg = root / "package.json"
        if pkg.exists():
            has_test_script = False
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                scripts = data.get("scripts") or {}
                has_test_script = "test" in scripts
            except (json.JSONDecodeError, OSError):
                has_test_script = False
            if has_test_script:
                return RepoProfile(
                    kind="node",
                    root=root,
                    test_command=["npm", "test", "--", "--watchAll=false"],
                    reason="package.json scripts.test",
                )
            # Next/React without test script — skip rather than inventing
            return RepoProfile(
                kind="node",
                root=root,
                test_command=[],
                reason="package.json without test script",
            )

        # Python
        has_pytest_ini = (root / "pytest.ini").exists()
        has_pyproject = (root / "pyproject.toml").exists()
        has_tests = (root / "tests").is_dir() or (root / "test").is_dir()
        if has_pytest_ini or has_pyproject or has_tests or (root / "requirements.txt").exists():
            test_dir = "tests" if (root / "tests").is_dir() else (
                "test" if (root / "test").is_dir() else "."
            )
            # Prefer pytest when configured or tests/ present
            if has_pytest_ini or has_tests or _pyproject_has_pytest(root / "pyproject.toml"):
                return RepoProfile(
                    kind="python",
                    root=root,
                    test_command=["python", "-m", "pytest", test_dir, "-q", "--tb=short"],
                    reason="python pytest config" if has_pytest_ini or has_pyproject else "tests/",
                )
            return RepoProfile(
                kind="python",
                root=root,
                test_command=[],
                reason="python project without detectable tests",
            )

        return RepoProfile(
            kind="unknown",
            root=root,
            test_command=[],
            reason="no known manifest",
        )


def _pyproject_has_pytest(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "pytest" in text
