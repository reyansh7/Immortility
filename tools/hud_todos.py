"""HUD todos JSON persistence (no SQLite)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _path() -> Path:
    try:
        from core.repo_paths import hud_todos_path

        return hud_todos_path()
    except Exception:
        p = Path("memory/data")
        p.mkdir(parents=True, exist_ok=True)
        return p / "hud_todos.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def list_todos() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_load())


def create_todo(text: str, due_at: str | None = None) -> dict[str, Any]:
    item = {
        "id": uuid.uuid4().hex[:12],
        "text": (text or "").strip()[:500],
        "due_at": (due_at or "").strip() or None,
        "done": False,
        "due_fired": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _LOCK:
        items = _load()
        items.insert(0, item)
        _save(items)
    return item


def patch_todo(todo_id: str, **fields: Any) -> dict[str, Any] | None:
    with _LOCK:
        items = _load()
        for it in items:
            if it.get("id") == todo_id:
                if "done" in fields and fields["done"] is not None:
                    it["done"] = bool(fields["done"])
                if "due_fired" in fields and fields["due_fired"] is not None:
                    it["due_fired"] = bool(fields["due_fired"])
                if "text" in fields and fields["text"] is not None:
                    it["text"] = str(fields["text"])[:500]
                _save(items)
                return dict(it)
    return None


def delete_todo(todo_id: str) -> bool:
    with _LOCK:
        items = _load()
        new = [it for it in items if it.get("id") != todo_id]
        if len(new) == len(items):
            return False
        _save(new)
        return True


def fire_due_todos() -> list[dict[str, Any]]:
    """Mark due items; return newly fired ones."""
    now = time.time()
    fired: list[dict[str, Any]] = []
    with _LOCK:
        items = _load()
        changed = False
        for it in items:
            if it.get("done") or it.get("due_fired") or not it.get("due_at"):
                continue
            try:
                # Accept ISO with Z or local naive
                raw = str(it["due_at"]).replace("Z", "+00:00")
                from datetime import datetime

                dt = datetime.fromisoformat(raw)
                ts = dt.timestamp()
            except Exception:
                continue
            if ts <= now:
                it["due_fired"] = True
                changed = True
                fired.append(dict(it))
        if changed:
            _save(items)
    return fired
