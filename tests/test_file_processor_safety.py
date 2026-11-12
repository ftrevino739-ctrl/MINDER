"""Safety tests for file_processor code execution, outputs and archive extraction."""
from __future__ import annotations

import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from actions import file_processor as module


class FileProcessorSafetyTests(unittest.TestCase):
    def _home_temp(self):
        return tempfile.TemporaryDirectory(dir=Path.home())

    def test_output_path_uses_new_name_when_previous_output_exists(self):
        with self._home_temp() as folder:
            source = Path(folder) / "photo.png"
            source.write_bytes(b"original")
            (Path(folder) / "photo_result.txt").write_text("keep me", encoding="utf-8")
            out = module._output_path(source, "result", ".txt")
            self.assertEqual(out.name, "photo_result_2.txt")
            self.assertFalse(out.exists())

    def test_code_execution_requires_approval_and_checks_file_unchanged(self):
        with self._home_temp() as folder:
            source = Path(folder) / "runme.py"
            source.write_text("print('safe')", encoding="utf-8")
            captured = {}
            with (
                patch.object(
                    module.confirm, "request",
                    side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
                ),
                patch.object(module.subprocess, "run") as run,
            ):
                result = module._process_code(source, "run", {})
                self.assertIn("[CONFIRMATION_PENDING]", result)
                self.assertIn(str(source), captured["detail"])
                run.assert_not_called()
                run.return_value.returncode = 0
                captured["run"]()
                run.assert_called_once()
                self.assertIs(run.call_args.kwargs["shell"], False)

    def test_code_execution_refuses_file_changed_after_approval(self):
        with self._home_temp() as folder:
            source = Path(folder) / "runme.py"
            source.write_text("print('old')", encoding="utf-8")
            captured = {}
            with patch.object(
                module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ):
                result = module._process_code(source, "run", {})
            self.assertIn("[CONFIRMATION_PENDING]", result)
            source.write_text("print('changed')", encoding="utf-8")
            refused = captured["run"]()
            self.assertIn("changed while execution approval was pending", refused)

    def test_zip_slip_path_is_rejected_without_extraction(self):
        with self._home_temp() as folder:
            root = Path(folder)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.txt", "escape")
            dest = root / "unpacked"
            with patch.object(module.confirm, "request") as request:
                result = module._process_archive(
                    archive_path, "extract", {"destination": str(dest)}
                )
            self.assertIn("unsafe archive path", result)
            self.assertFalse(dest.exists())
            request.assert_not_called()

    def test_zip_extract_waits_for_approval_and_will_not_overwrite(self):
        with self._home_temp() as folder:
            root = Path(folder)
            archive_path = root / "bundle.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("docs/readme.txt", "archive data")
            dest = root / "unpacked"
            captured = {}
            with patch.object(
                module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ):
                result = module._process_archive(
                    archive_path, "extract", {"destination": str(dest)}
                )
                self.assertIn("[CONFIRMATION_PENDING]", result)
                self.assertFalse(dest.exists())
                extracted = captured["run"]()
            self.assertIn("status=done", extracted)
            self.assertEqual((dest / "docs" / "readme.txt").read_text(encoding="utf-8"), "archive data")

    def test_archive_destination_must_be_inside_home_and_empty(self):
        with self._home_temp() as folder, tempfile.TemporaryDirectory() as outside:
            root = Path(folder)
            archive_path = root / "bundle.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("readme.txt", "hello")
            with patch.object(module.confirm, "request") as request:
                refused = module._process_archive(
                    archive_path, "extract", {"destination": str(Path(outside) / "out")}
                )
                self.assertIn("inside the user's home folder", refused)
                request.assert_not_called()
            existing = root / "existing"
            existing.mkdir()
            (existing / "keep.txt").write_text("keep", encoding="utf-8")
            result = module._process_archive(
                archive_path, "extract", {"destination": str(existing)}
            )
            self.assertIn("must be empty", result)
            self.assertEqual((existing / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_archive_bytes_changed_after_review_are_not_extracted(self):
        with self._home_temp() as folder:
            root = Path(folder)
            archive_path = root / "bundle.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("draft.txt", "old-data")
            dest = root / "unpacked"
            captured = {}
            with patch.object(
                module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ):
                result = module._process_archive(
                    archive_path, "extract", {"destination": str(dest)}
                )
            self.assertIn("[CONFIRMATION_PENDING]", result)

            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("draft.txt", "new-data")
            refused = captured["run"]()
            self.assertIn("archive bytes changed while extraction approval was pending", refused)
            self.assertFalse(dest.exists())

    def test_windows_reserved_and_invalid_archive_names_are_rejected(self):
        with self._home_temp() as folder:
            root = Path(folder)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("CON.txt", "reserved")
            entries, error = module._archive_plan(archive_path)
            self.assertEqual(entries, [])
            self.assertIn("unsafe archive path", error)

    def test_tar_symlink_entries_are_rejected(self):
        with self._home_temp() as folder:
            root = Path(folder)
            archive_path = root / "bad.tar"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            entries, error = module._archive_plan(archive_path)
            self.assertEqual(entries, [])
            self.assertIn("links and special archive entries", error)


if __name__ == "__main__":
    unittest.main()
