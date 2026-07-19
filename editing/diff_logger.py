"""Unified diff logging for all file modifications."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from editing.patch_generator import PatchGenerator

logger = logging.getLogger(__name__)

DIFF_DIR = Path("logs/diffs")


class DiffLogger:
    """Writes unified diffs for every file modification."""

    def __init__(self, diff_dir: Path | str = DIFF_DIR) -> None:
        self._dir = Path(diff_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def log_edit(
        self,
        filepath: str,
        old_content: str,
        new_content: str,
        operation: str = "edit",
    ) -> str:
        """Write a unified diff and return its path."""
        if old_content == new_content:
            return ""

        diff_text = PatchGenerator.generate_unified_diff(
            old_content, new_content, fromfile=f"a/{filepath}", tofile=f"b/{filepath}"
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        safe_name = Path(filepath).name.replace(" ", "_")
        diff_path = self._dir / f"{timestamp}_{operation}_{safe_name}.diff"
        diff_path.write_text(diff_text, encoding="utf-8")
        logger.info("Diff logged: %s (%s)", diff_path, operation)
        return str(diff_path)
