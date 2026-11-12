"""Tests for DevAgent's VS Code launcher: no shell command parsing."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import dev_agent


class OpenVSCodeSafetyTests(unittest.TestCase):
    def test_windows_code_exe_is_launched_without_a_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "Code.exe"
            exe.write_bytes(b"")
            project = root / "MINDER & Projects"
            project.mkdir()

            with (
                patch.object(dev_agent.sys, "platform", "win32"),
                patch.dict(dev_agent.os.environ, {
                    "LOCALAPPDATA": temp,
                    "PROGRAMFILES": str(root / "Program Files"),
                    "PROGRAMFILES(X86)": str(root / "Program Files (x86)"),
                }, clear=False),
                patch.object(dev_agent.shutil, "which", return_value=None),
                patch.object(dev_agent.subprocess, "Popen") as popen,
                patch.object(dev_agent.time, "sleep"),
            ):
                result = dev_agent._open_vscode(project)

        self.assertTrue(result)
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [str(exe), str(project)])
        self.assertIs(kwargs["shell"], False)

    def test_windows_code_cmd_shim_is_not_run_through_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            cmd_shim = project / "code.cmd"
            cmd_shim.write_text("@echo off", encoding="utf-8")

            with (
                patch.object(dev_agent.sys, "platform", "win32"),
                patch.dict(dev_agent.os.environ, {
                    "LOCALAPPDATA": str(project / "missing-local"),
                    "PROGRAMFILES": str(project / "missing-program-files"),
                    "PROGRAMFILES(X86)": str(project / "missing-program-files-x86"),
                }, clear=False),
                patch.object(dev_agent.shutil, "which", return_value=str(cmd_shim)),
                patch.object(dev_agent.subprocess, "Popen") as popen,
            ):
                result = dev_agent._open_vscode(project)

        self.assertFalse(result)
        popen.assert_not_called()

    def test_posix_code_launcher_is_also_shell_free(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with (
                patch.object(dev_agent.sys, "platform", "linux"),
                patch.object(dev_agent.shutil, "which", return_value="/usr/bin/code"),
                patch.object(dev_agent.subprocess, "Popen") as popen,
                patch.object(dev_agent.time, "sleep"),
            ):
                result = dev_agent._open_vscode(project)

        self.assertTrue(result)
        self.assertIs(popen.call_args.kwargs["shell"], False)


if __name__ == "__main__":
    unittest.main()
