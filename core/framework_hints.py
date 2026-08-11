"""Detect project framework and inject relevant tool-prompt hints (no hardcoding Next.js into core)."""

from __future__ import annotations

from pathlib import Path


def get_framework_hints(project_root: str | Path | None) -> str:
    """Return short framework-specific guidance for the action engine."""
    if not project_root:
        return ""
    root = Path(project_root)
    if not root.is_dir():
        return ""

    hints: list[str] = []

    next_cfgs = (
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
    )
    if any((root / n).is_file() for n in next_cfgs) or (root / "package.json").is_file():
        pkg = root / "package.json"
        is_next = any((root / n).is_file() for n in next_cfgs)
        if not is_next and pkg.is_file():
            try:
                text = pkg.read_text(encoding="utf-8", errors="ignore").lower()
                is_next = "next" in text
            except OSError:
                is_next = False
        if is_next:
            hints.append(
                "Next.js detected: prefer App Router paths under src/app/ when present; "
                "globals.css is usually src/app/globals.css (not src/globals.css)."
            )
            hints.append(
                "For .tsx/.jsx: prefer write_file with the full corrected file if edit_file fails twice."
            )

    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        hints.append(
            "Python project detected: prefer package-relative imports; "
            "run tests with pytest when available."
        )

    if (root / "Cargo.toml").is_file():
        hints.append("Rust project detected: respect Cargo.toml workspace layout.")

    if (root / "go.mod").is_file():
        hints.append("Go project detected: keep module paths consistent with go.mod.")

    return "\n".join(f"- {h}" for h in hints)
