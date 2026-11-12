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

    def test_delete_requires_approval_before_moving_target_to_trash(self) -> None:
        target = self.root / "important.txt"
        target.write_text("keep me", encoding="utf-8")
        captured = {}

        with (
            patch.object(
                file_controller.confirm_gate, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ) as gate,
            patch.object(file_controller, "_safe_trash", return_value="Moved to Trash: important.txt") as trash,
        ):
            result = file_controller.delete_file(str(self.root), "important.txt")

        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        gate.assert_called_once()
        trash.assert_not_called()
        self.assertTrue(target.exists())
        self.assertIn(str(target), captured["detail"])
        self.assertIn("not permanently erase", captured["detail"])
        moved = captured["run"]()
        self.assertIn("Moved to Trash", moved)
        trash.assert_called_once_with(target)

    def test_delete_refuses_if_file_changes_while_approval_is_pending(self) -> None:
        target = self.root / "important.txt"
        target.write_text("original", encoding="utf-8")
        captured = {}

        with (
            patch.object(
                file_controller.confirm_gate, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ),
            patch.object(file_controller, "_safe_trash") as trash,
        ):
            file_controller.delete_file(str(self.root), "important.txt")

        target.write_text("user changed this", encoding="utf-8")
        result = captured["run"]()
        self.assertIn("changed while approval was pending", result)
        trash.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "user changed this")

    def test_delete_rejects_symbolic_links(self) -> None:
        target = self.root / "link.txt"
        source = self.root / "source.txt"
        source.write_text("keep", encoding="utf-8")
        try:
            target.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available in this environment")
        with patch.object(file_controller.confirm_gate, "request") as gate:
            result = file_controller.delete_file(str(self.root), "link.txt")
        self.assertIn("symbolic links", result)
        gate.assert_not_called()
        self.assertEqual(source.read_text(encoding="utf-8"), "keep")

    def test_file_controller_organize_uses_approved_desktop_flow(self) -> None:
        from actions import desktop as desktop_module

        with patch.object(
            desktop_module, "organize_desktop", return_value="[CONFIRMATION_PENDING]"
        ) as approved:
            result = file_controller.file_controller({"action": "organize_desktop"})

        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        approved.assert_called_once_with()

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

    def test_undo_created_file_keeps_later_user_changes(self) -> None:
        target = self.root / "created.txt"
        result = file_controller.create_file(str(self.root), "created.txt", "assistant")

        self.assertEqual(result, "File created: created.txt")
        target.write_text("user edit", encoding="utf-8")
        undo_result = undo.undo_last()

        self.assertIn("changed after it was created", undo_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "user edit")

    def test_undo_write_keeps_later_user_changes(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("before", encoding="utf-8")
        file_controller.write_file(str(self.root), "notes.txt", "assistant edit")
        target.write_text("later user edit", encoding="utf-8")

        undo_result = undo.undo_last()

        self.assertIn("changed after MINDER wrote it", undo_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "later user edit")

    def test_undo_copy_keeps_a_copy_that_the_user_modified(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "destination.txt"
        source.write_text("source", encoding="utf-8")
        result = file_controller.copy_file(str(self.root), "source.txt", str(destination))

        self.assertIn("Copied:", result)
        destination.write_text("user version", encoding="utf-8")
        undo_result = undo.undo_last()

        self.assertIn("changed after it was created", undo_result)
        self.assertEqual(destination.read_text(encoding="utf-8"), "user version")
        self.assertEqual(source.read_text(encoding="utf-8"), "source")

    def test_undo_move_does_not_replace_a_new_file_at_original_path(self) -> None:
        source = self.root / "source.txt"
        destination = self.root / "moved.txt"
        source.write_text("original", encoding="utf-8")
        result = file_controller.move_file(str(self.root), "source.txt", str(destination))

        self.assertIn("Moved:", result)
        source.write_text("new user file", encoding="utf-8")
        undo_result = undo.undo_last()

        self.assertIn("now exists at the original location", undo_result)
        self.assertEqual(source.read_text(encoding="utf-8"), "new user file")
        self.assertEqual(destination.read_text(encoding="utf-8"), "original")

    def test_undo_directory_copy_keeps_new_files_added_by_user(self) -> None:
        source = self.root / "source-folder"
        destination = self.root / "destination-folder"
        source.mkdir()
        (source / "original.txt").write_text("original", encoding="utf-8")
        result = file_controller.copy_file(str(self.root), "source-folder", str(destination))

        self.assertIn("Copied:", result)
        (destination / "user-added.txt").write_text("keep me", encoding="utf-8")
        undo_result = undo.undo_last()

        self.assertIn("changed after it was created", undo_result)
        self.assertTrue((destination / "original.txt").exists())
        self.assertEqual((destination / "user-added.txt").read_text(encoding="utf-8"), "keep me")

    def test_new_file_write_is_reversible_without_confirmation(self) -> None:
        target = self.root / "new.txt"
        result = file_controller.write_file(str(self.root), "new.txt", "hello")

        self.assertEqual(result, "Written to: new.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        undo.undo_last()
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
