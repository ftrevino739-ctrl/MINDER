"""Safety regression tests for MINDER file operations."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import file_controller
from core import undo


class FileControllerSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self._old_roots = file_controller._SAFE_ROOTS
        self._old_limit = file_controller._UNDO_CONTENT_LIMIT
        file_controller._SAFE_ROOTS = [self.root]
        undo.clear()

    def tearDown(self) -> None:
        undo.clear()
        file_controller._SAFE_ROOTS = self._old_roots
        file_controller._UNDO_CONTENT_LIMIT = self._old_limit
        self._temporary.cleanup()

    def test_create_file_never_overwrites_an_existing_file(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("keep this", encoding="utf-8")

        result = file_controller.create_file(str(self.root), "notes.txt", "replace")

        self.assertIn("already exists", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep this")

    def test_copy_refuses_to_overwrite_an_existing_destination(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_text("source", encoding="utf-8")
        destination.write_text("destination", encoding="utf-8")

        result = file_controller.copy_file(
            str(self.root), "source.txt", str(destination)
        )

        self.assertIn("Destination already exists", result)
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(destination.read_text(encoding="utf-8"), "destination")

    def test_move_refuses_to_overwrite_an_existing_destination(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_text("source", encoding="utf-8")
        destination.write_text("destination", encoding="utf-8")

        result = file_controller.move_file(
            str(self.root), "source.txt", str(destination)
        )

        self.assertIn("Destination already exists", result)
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(destination.read_text(encoding="utf-8"), "destination")

    def test_small_utf8_write_can_be_undone(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("before", encoding="utf-8")

        result = file_controller.write_file(str(self.root), "notes.txt", "after")

        self.assertEqual(result, "Written to: notes.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "after")
        undo_result = undo.undo_last()
        self.assertIn("Restored the previous contents", undo_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")

    def test_large_existing_file_requires_confirmation_before_write(self) -> None:
        target = self.root / "large.txt"
        target.write_text("original-content", encoding="utf-8")
        file_controller._UNDO_CONTENT_LIMIT = 4
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(file_controller.confirm_gate, "request", side_effect=request) as gate:
            result = file_controller.write_file(str(self.root), "large.txt", "replacement")

        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        gate.assert_called_once()
        self.assertEqual(target.read_text(encoding="utf-8"), "original-content")
        self.assertIn("cannot be undone", captured["detail"])
        performed = captured["run"]()
        self.assertIn("cannot be undone", performed)
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")

    def test_non_utf8_existing_file_requires_confirmation(self) -> None:
        target = self.root / "binary.dat"
        target.write_bytes(b"\xff\xfe\x00\x01")
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(file_controller.confirm_gate, "request", side_effect=request) as gate:
            result = file_controller.write_file(str(self.root), "binary.dat", "replacement")

        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        gate.assert_called_once()
        self.assertEqual(target.read_bytes(), b"\xff\xfe\x00\x01")
        captured["run"]()
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")

    def test_file_changed_while_confirmation_is_pending_is_not_overwritten(self) -> None:
        target = self.root / "large.txt"
        target.write_text("original-content", encoding="utf-8")
        file_controller._UNDO_CONTENT_LIMIT = 4
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(file_controller.confirm_gate, "request", side_effect=request):
            file_controller.write_file(str(self.root), "large.txt", "replacement")

        target.write_text("changed by the user", encoding="utf-8")
        result = captured["run"]()
        self.assertIn("changed while awaiting", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed by the user")

    def test_new_file_write_is_reversible_without_confirmation(self) -> None:
        target = self.root / "new.txt"
        result = file_controller.write_file(str(self.root), "new.txt", "hello")

        self.assertEqual(result, "Written to: new.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        undo.undo_last()
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
