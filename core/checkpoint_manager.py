"""Checkpoint manager — snapshot workflow context and modified files."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CheckpointManager:
    def __init__(self, root: str | Path = ".checkpoints") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, workflow_id: str, context: dict[str, Any], files: list[str]) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ckpt_dir = self.root / f"{workflow_id}_{ts}"
        ckpt_dir.mkdir(parents=True, exist_ok=True)

        (ckpt_dir / "context.json").write_text(
            json.dumps(context, indent=2), encoding="utf-8"
        )
        files_dir = ckpt_dir / "files"
        files_dir.mkdir(exist_ok=True)

        manifest: dict[str, str] = {}
        used_keys: set[str] = set()
        for fpath in files:
            src = Path(fpath)
            if not src.is_file():
                continue
            key = src.name
            if key in used_keys:
                key = f"{src.parent.name}_{src.name}"
            used_keys.add(key)
            shutil.copy2(src, files_dir / key)
            manifest[key] = str(src.resolve())

        (ckpt_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return str(ckpt_dir)

    def rollback(self, checkpoint_dir: str) -> list[str]:
        """Restore files from checkpoint manifest to their original paths."""
        ckpt = Path(checkpoint_dir)
        manifest_path = ckpt / "manifest.json"
        files_dir = ckpt / "files"
        restored: list[str] = []

        if manifest_path.exists():
            manifest: dict[str, str] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            for key, orig_path in manifest.items():
                backup = files_dir / key
                if not backup.is_file() or not orig_path:
                    continue
                dest = Path(orig_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, dest)
                restored.append(str(dest))
            return restored

        # Legacy checkpoints without manifest — cannot restore reliably
        if files_dir.exists():
            for backup in files_dir.iterdir():
                restored.append(str(backup))
        return restored

    def list_checkpoints(self, workflow_id: str) -> list[str]:
        return sorted(
            str(p) for p in self.root.glob(f"{workflow_id}_*") if p.is_dir()
        )
