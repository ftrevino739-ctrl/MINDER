"""Tests for all file_processor derived output collision guards."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import file_processor as module


class FileProcessorOutputSafetyTests(unittest.TestCase):
    def _home_temp(self):
        return tempfile.TemporaryDirectory(dir=Path.home())

    def test_exclusive_text_output_refuses_existing_file(self):
        with self._home_temp() as folder:
            path = Path(folder) / "result.txt"
            path.write_text("user data", encoding="utf-8")
            result = module._write_new_text(path, "new output")
            self.assertIn("status=failed", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "user data")

    def test_exclusive_text_output_creates_file(self):
        with self._home_temp() as folder:
            path = Path(folder) / "result.txt"
            result = module._write_new_text(path, "new output")
            self.assertEqual(result, f"Saved to: {path}")
            self.assertEqual(path.read_text(encoding="utf-8"), "new output")

    def test_image_save_refuses_existing_output(self):
        from PIL import Image
        with self._home_temp() as folder:
            path = Path(folder) / "image.png"
            path.write_bytes(b"user image")
            result = module._save_pil_image(Image.new("RGB", (1, 1)), path, "PNG")
            self.assertIn("status=failed", result)
            self.assertEqual(path.read_bytes(), b"user image")

    def test_image_save_creates_new_output(self):
        from PIL import Image
        with self._home_temp() as folder:
            path = Path(folder) / "image.png"
            result = module._save_pil_image(Image.new("RGB", (1, 1)), path, "PNG")
            self.assertEqual(result, f"Saved to: {path}")
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                self.assertEqual(image.size, (1, 1))

    def test_ffmpeg_runner_inserts_no_overwrite_flag_and_checks_shell_false(self):
        with patch.object(module.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            module._run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4"], 10)
        command = run.call_args.args[0]
        self.assertEqual(command[1], "-n")
        self.assertNotIn("-y", command)
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_ffmpeg_runner_rejects_explicit_overwrite_flag(self):
        with patch.object(module.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                module._run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4", "-y"], 10)
        run.assert_not_called()

    def test_ffmpeg_nonzero_exit_is_reported_as_failure(self):
        with patch.object(module.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "output exists"
            with self.assertRaisesRegex(RuntimeError, "output exists"):
                module._run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4"], 10)


if __name__ == "__main__":
    unittest.main()
