"""Safety tests for DevAgent's generated project paths and commands."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import dev_agent


class DevAgentProjectSafetyTests(unittest.TestCase):
    def test_project_file_path_rejects_absolute_and_traversal_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new-project"
            root.mkdir()
            for value in (
                "../outside.py",
                "src/../../outside.py",
                "/tmp/outside.py",
                r"..\outside.py",
                r"C:\Users\Public\outside.py",
                "",
                ".",
            ):
                with self.subTest(path=value):
                    self.assertIsNone(dev_agent._safe_project_file_path(root, value))

    def test_project_file_path_accepts_only_inside_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new-project"
            root.mkdir()
            self.assertEqual(
                dev_agent._safe_project_file_path(root, "src/main.py"),
                (root / "src" / "main.py").resolve(),
            )

    def test_generated_project_write_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("user data", encoding="utf-8")
            with patch.object(dev_agent, "_get_model") as model:
                model.return_value.generate_content.return_value.text = "print('hello')"
                with self.assertRaises(FileExistsError):
                    dev_agent._write_file(
                        {"path": "main.py", "description": "entry point"},
                        "test project",
                        [{"path": "main.py"}],
                        "python",
                        root,
                        {},
                    )
            self.assertEqual((root / "main.py").read_text(encoding="utf-8"), "user data")

    def test_run_project_requires_human_approval_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            captured = {}
            with (
                patch.object(dev_agent, "_projects_root", return_value=root),
                patch.object(
                    dev_agent.confirm,
                    "request",
                    side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
                ),
                patch.object(
                    dev_agent.subprocess, "run",
                    return_value=subprocess.CompletedProcess(
                        args=["python"], returncode=0, stdout="hello", stderr="",
                    ),
                ) as run,
            ):
                result = dev_agent._run_project("python main.py --name 'my app'", project, 60)
                self.assertIn("[CONFIRMATION_PENDING]", result)
                run.assert_not_called()
                self.assertIn("python", captured["detail"])
                self.assertIn("'my app'", captured["detail"])
                approved = captured["run"]()
                self.assertIn("status=done", approved)
                run.assert_called_once()
                self.assertIs(run.call_args.kwargs["shell"], False)

    def test_run_project_refuses_projects_outside_managed_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "projects"
            other = Path(temp) / "other"
            root.mkdir()
            other.mkdir()
            with (
                patch.object(dev_agent, "_projects_root", return_value=root),
                patch.object(dev_agent.subprocess, "run") as run,
            ):
                result = dev_agent._run_project("python main.py", other)
            self.assertIn("status=failed", result)
            run.assert_not_called()

    def test_dependency_specifiers_reject_direct_urls_and_shell_flags(self):
        for dependency in ("--index-url=https://evil.test", "git+https://evil.test/pkg.git",
                           "../local-package", "pkg @ https://evil.test/pkg.whl"):
            with self.subTest(dependency=dependency):
                self.assertIsNone(dev_agent._SAFE_REQUIREMENT.fullmatch(dependency))

    def test_missing_dependency_installation_requires_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captured = {}
            with (
                patch.object(dev_agent.subprocess, "run") as run,
                patch.object(
                    dev_agent.confirm,
                    "request",
                    side_effect=lambda **kwargs: captured.update(kwargs) or "[CONFIRMATION_PENDING]",
                ),
            ):
                run.return_value.returncode = 1
                result = dev_agent._install_dependencies(["requests>=2.0"], root)
                self.assertIn("[CONFIRMATION_PENDING]", result)
                self.assertIn("requests>=2.0", captured["detail"])
                self.assertEqual(run.call_count, 1)  # only "pip show", never "pip install"
                captured["run"]()
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0][-2:], ["install", "requests>=2.0"])
                self.assertIs(run.call_args.kwargs["shell"], False)


if __name__ == "__main__":
    unittest.main()
