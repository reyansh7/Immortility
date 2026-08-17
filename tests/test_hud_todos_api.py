"""CRUD smoke tests for HUD todos JSON helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class HudTodosApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "hud_todos.json"
        self._patch = mock.patch("tools.hud_todos._path", return_value=self.path)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_create_list_patch_delete(self) -> None:
        from tools.hud_todos import create_todo, delete_todo, list_todos, patch_todo

        item = create_todo("Buy milk", due_at="2099-01-01T12:00:00+00:00")
        self.assertTrue(item["id"])
        self.assertEqual(item["text"], "Buy milk")
        self.assertFalse(item["done"])
        self.assertFalse(item["due_fired"])

        items = list_todos()
        self.assertEqual(len(items), 1)

        patched = patch_todo(item["id"], done=True)
        assert patched is not None
        self.assertTrue(patched["done"])

        self.assertTrue(delete_todo(item["id"]))
        self.assertEqual(list_todos(), [])
        self.assertTrue(self.path.is_file())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])

    def test_fire_due_todos(self) -> None:
        from tools.hud_todos import create_todo, fire_due_todos, list_todos

        create_todo("Past due", due_at="2020-01-01T00:00:00+00:00")
        create_todo("Future", due_at="2099-01-01T00:00:00+00:00")
        fired = fire_due_todos()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["text"], "Past due")
        again = fire_due_todos()
        self.assertEqual(again, [])
        todos = {t["text"]: t for t in list_todos()}
        self.assertTrue(todos["Past due"]["due_fired"])
        self.assertFalse(todos["Future"]["due_fired"])


if __name__ == "__main__":
    unittest.main()
