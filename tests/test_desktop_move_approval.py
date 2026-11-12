"""Confirmation and undo-path tests for desktop file organization."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import desktop as desktop_module


class DesktopMoveApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.desktop = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def _capture(self):
        captured = {}
        patcher = patch.object(
            desktop_module.confirm,
            "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def test_organize_shows_exact_moves_and_waits_for_human_approval(self):
        (self.desktop / "photo.png").write_bytes(b"png")
        (self.desktop / "notes.txt").write_text("notes", encoding="utf-8")
        captured = self._capture()
        with patch.object(desktop_module, "_get_desktop", return_value=self.desktop):
            result = desktop_module.organize_desktop("by_type")

        self.assertEqual(result, "PENDING")
        self.assertEqual(captured["title"], "Approve organizing 2 desktop files")
        self.assertIn("photo.png  →  Images/", captured["detail"])
        self.assertIn("notes.txt  →  Documents/", captured["detail"])
        self.assertTrue((self.desktop / "photo.png").exists())
        self.assertTrue((self.desktop / "notes.txt").exists())

    def test_no_file_moves_when_desktop_contents_change_before_approval(self):
        (self.desktop / "notes.txt").write_text("notes", encoding="utf-8")
        captured = self._capture()
        with (
            patch.object(desktop_module, "_get_desktop", return_value=self.desktop),
            patch("actions.file_controller.create_folder") as create_folder,
            patch("actions.file_controller.move_file") as move_file,
        ):
            self.assertEqual(desktop_module.organize_desktop("by_type"), "PENDING")
            (self.desktop / "new.txt").write_text("new", encoding="utf-8")
            result = captured["run"]()

        self.assertIn("files changed while approval was pending", result)
        create_folder.assert_not_called()
        move_file.assert_not_called()
        self.assertTrue((self.desktop / "notes.txt").exists())

    def test_approved_organization_uses_file_controller_move_and_folder_helpers(self):
        (self.desktop / "notes.txt").write_text("notes", encoding="utf-8")
        captured = self._capture()
        target_dir = self.desktop / "Documents"

        def create_folder(path, name):
            target_dir.mkdir(exist_ok=True)
            return "Folder created: Documents"

        def move_file(path, name, destination):
            source = Path(path) / name
            target = Path(destination) / name
            target.parent.mkdir(exist_ok=True)
            source.rename(target)
            return f"Moved: {name} → Documents/"

        with (
            patch.object(desktop_module, "_get_desktop", return_value=self.desktop),
            patch("actions.file_controller.create_folder", side_effect=create_folder) as create,
            patch("actions.file_controller.move_file", side_effect=move_file) as move,
        ):
            result = desktop_module.organize_desktop("by_type")
            self.assertEqual(result, "PENDING")
            self.assertTrue((self.desktop / "notes.txt").exists())
            approved = captured["run"]()

        self.assertIn("status=done", approved)
        self.assertFalse((self.desktop / "notes.txt").exists())
        self.assertTrue((target_dir / "notes.txt").exists())
        create.assert_called_once()
        move.assert_called_once()

    def test_clean_desktop_also_requires_approval_before_archiving(self):
        (self.desktop / "report.pdf").write_text("report", encoding="utf-8")
        captured = self._capture()
        with patch.object(desktop_module, "_get_desktop", return_value=self.desktop):
            result = desktop_module.clean_desktop()

        self.assertEqual(result, "PENDING")
        self.assertIn("report.pdf", captured["detail"])
        self.assertTrue((self.desktop / "report.pdf").exists())

    def test_empty_desktop_does_not_create_a_confirmation(self):
        with (
            patch.object(desktop_module, "_get_desktop", return_value=self.desktop),
            patch.object(desktop_module.confirm, "request") as request,
        ):
            result = desktop_module.clean_desktop()
        self.assertIn("No eligible desktop files", result)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
