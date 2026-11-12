from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import flight_finder, youtube_video


class ReportOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.desktop = self.root / "Desktop"

    def tearDown(self):
        self.temp.cleanup()

    def test_flight_report_sanitizes_names_and_uses_exclusive_output(self):
        with (
            patch.object(flight_finder.Path, "home", return_value=self.root),
            patch.object(flight_finder, "is_windows", return_value=True),
            patch("actions.flight_finder.subprocess.Popen"),
        ):
            first = Path(flight_finder._save_to_desktop("report", "../../secret", "C:\\\\outside"))
            second = Path(flight_finder._save_to_desktop("report-2", "../../secret", "C:\\\\outside"))

        self.assertEqual(first.parent, self.desktop)
        self.assertEqual(second.parent, self.desktop)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "report")
        self.assertEqual(second.read_text(encoding="utf-8"), "report-2")
        self.assertNotIn("..", first.name)
        self.assertNotIn(":", first.name)

    def test_youtube_summary_never_overwrites_same_second_output(self):
        class FixedDateTime:
            @staticmethod
            def now():
                class Moment:
                    @staticmethod
                    def strftime(fmt):
                        return "20261111_200000" if fmt == "%Y%m%d_%H%M%S" else "2026-11-11 20:00"
                return Moment()

        with (
            patch.object(youtube_video.Path, "home", return_value=self.root),
            patch.object(youtube_video, "datetime", FixedDateTime),
            patch.object(youtube_video, "is_windows", return_value=True),
            patch("actions.youtube_video.subprocess.Popen"),
        ):
            first = Path(youtube_video._save_summary("first", "https://example.test/video"))
            second = Path(youtube_video._save_summary("second", "https://example.test/video"))

        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8").splitlines()[-1], "first")
        self.assertEqual(second.read_text(encoding="utf-8").splitlines()[-1], "second")
        self.assertTrue(first.name.startswith("youtube_summary_"))


if __name__ == "__main__":
    unittest.main()
