"""Safety tests for code_helper file access, overwrite and execution."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import code_helper


class CodeHelperSafetyTests(unittest.TestCase):
    def _home_temp(self):
        return tempfile.TemporaryDirectory(dir=Path.home())

    def test_read_and_write_are_restricted_to_the_user_home(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "secret.py"
            path.write_text("private", encoding="utf-8")
            text, error = code_helper._read_file(str(path))
            self.assertEqual(text, "")
            self.assertIn("Access denied", error)
            result = code_helper._save_file(path, "replacement")
            self.assertIn("status=failed", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "private")

    def test_new_file_is_created_exclusively(self):
        with self._home_temp() as folder:
            path = Path(folder) / "new.py"
            result = code_helper._save_file(path, "print('hello')")
            self.assertIn("Saved to:", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "print('hello')")

    def test_existing_file_is_not_overwritten_before_human_approval(self):
        with self._home_temp() as folder:
            path = Path(folder) / "existing.py"
            path.write_text("old contents", encoding="utf-8")
            captured = {}
            with patch.object(
                code_helper.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ):
                result = code_helper._save_file(path, "new contents")

            self.assertIn("[CONFIRMATION_PENDING]", result)
            self.assertIn("old contents", captured["detail"])
            self.assertIn("new contents", captured["detail"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old contents")

            approved = captured["run"]()
            self.assertIn("status=done", approved)
            self.assertEqual(path.read_text(encoding="utf-8"), "new contents")

    def test_file_changed_while_approval_is_pending_is_not_overwritten(self):
        with self._home_temp() as folder:
            path = Path(folder) / "existing.py"
            path.write_text("old contents", encoding="utf-8")
            captured = {}
            with patch.object(
                code_helper.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
            ):
                result = code_helper._save_file(path, "new contents")

            self.assertIn("[CONFIRMATION_PENDING]", result)
            path.write_text("user's newer contents", encoding="utf-8")
            refused = captured["run"]()
            self.assertIn("changed while approval was pending", refused)
            self.assertEqual(path.read_text(encoding="utf-8"), "user's newer contents")

    def test_run_outside_home_is_refused_without_prompt_or_execution(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "script.py"
            path.write_text("print('no')", encoding="utf-8")
            with (
                patch.object(code_helper.confirm, "request") as request,
                patch.object(code_helper.subprocess, "run") as run,
            ):
                result = code_helper._run_file(path, [], 10)
            self.assertIn("status=failed", result)
            request.assert_not_called()
            run.assert_not_called()

    def test_execution_shows_exact_command_and_waits_for_approval(self):
        with self._home_temp() as folder:
            path = Path(folder) / "script.py"
            path.write_text("print('hello')", encoding="utf-8")
            captured = {}
            completed = subprocess.CompletedProcess(
                args=["python", str(path)], returncode=0, stdout="hello", stderr="",
            )
            with (
                patch.object(
                    code_helper.confirm, "request",
                    side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
                ),
                patch.object(code_helper.subprocess, "run", return_value=completed) as run,
            ):
                result = code_helper._run_file(path, ["--name", "my project"], 15)
                self.assertIn("[CONFIRMATION_PENDING]", result)
                run.assert_not_called()
                self.assertIn(str(path), captured["detail"])
                self.assertIn("--name", captured["detail"])
                self.assertIn("'my project'", captured["detail"])
                approved = captured["run"]()

            self.assertIn("status=done", approved)
            run.assert_called_once()
            self.assertIs(run.call_args.kwargs["shell"], False)
            self.assertEqual(run.call_args.args[0][-2:], ["--name", "my project"])

    def test_execution_does_not_follow_symlink_outside_home(self):
        with self._home_temp() as folder, tempfile.TemporaryDirectory() as outside:
            actual = Path(outside) / "external.py"
            actual.write_text("print('external')", encoding="utf-8")
            link = Path(folder) / "link.py"
            try:
                link.symlink_to(actual)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not supported in this runner")
            with (
                patch.object(code_helper.confirm, "request") as request,
                patch.object(code_helper.subprocess, "run") as run,
            ):
                result = code_helper._run_file(link, [], 10)
            self.assertIn("status=failed", result)
            request.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
