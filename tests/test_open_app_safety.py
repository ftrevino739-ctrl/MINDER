from __future__ import annotations

import unittest
from unittest.mock import patch

from actions import open_app


class OpenAppSafetyTests(unittest.TestCase):
    def test_windows_blocks_dangerous_uri_schemes_before_os_handoff(self):
        dangerous = (
            "file:///C:/Users/Public/secret.txt",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "shell:AppsFolder\\\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            "ms-msdt:/id PCWDiagnostic",
            "ms-officecmd:ofv|u|https://example.test/attack",
            "search-ms:query=private",
        )
        with (
            patch.object(open_app.shutil, "which", return_value=None),
            patch.object(open_app.os, "startfile", create=True) as startfile,
            patch.object(open_app, "_desktop_search") as search,
        ):
            for value in dangerous:
                with self.subTest(value=value):
                    self.assertFalse(open_app._launch_windows(value))
        startfile.assert_not_called()
        search.assert_not_called()

    def test_windows_still_opens_safe_application_uris(self):
        with (
            patch.object(open_app.shutil, "which", return_value=None),
            patch.object(open_app.os, "startfile", create=True) as startfile,
        ):
            self.assertTrue(open_app._launch_windows("steam://run/730"))
            self.assertTrue(open_app._launch_windows("ms-settings:display"))
        self.assertEqual(startfile.call_count, 2)

    def test_windows_executable_launch_is_a_single_argument_without_shell(self):
        with (
            patch.object(open_app.shutil, "which", return_value=r"C:\\Windows\\notepad.exe"),
            patch.object(open_app.subprocess, "Popen") as popen,
        ):
            self.assertTrue(open_app._launch_windows("notepad.exe"))
        self.assertEqual(popen.call_args.args[0], [r"C:\\Windows\\notepad.exe"])
        self.assertNotIn("shell", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
