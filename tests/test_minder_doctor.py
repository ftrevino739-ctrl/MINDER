from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from memory import config_manager
import minder_doctor


class MinderDoctorTests(unittest.TestCase):
    def test_provider_report_never_prints_api_key_values(self):
        secret = "THIS-MUST-NEVER-APPEAR-IN-DOCTOR-OUTPUT"
        with (
            patch.object(config_manager, "get_provider_mode", return_value="pipeline"),
            patch.object(config_manager, "get_pipeline", return_value={
                "llm": "openai_compat@groq",
                "stt": "",
                "tts": "",
            }),
            patch.object(config_manager, "get_provider_config", return_value={
                "api_key": secret,
                "model": "test-model",
                "base_url": "https://api.example.test/v1",
            }),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                minder_doctor._provider_report()

        report = output.getvalue()
        self.assertIn("API key: configured (value hidden)", report)
        self.assertIn("test-model", report)
        self.assertNotIn(secret, report)

    def test_missing_module_is_reported_without_importing_it(self):
        with patch.object(minder_doctor.importlib.util, "find_spec", return_value=None):
            self.assertFalse(minder_doctor._module_available("not_a_real_dependency"))


    def test_browser_report_distinguishes_installed_and_missing_runtimes(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            chromium = Path(folder) / "chrome.exe"
            chromium.write_bytes(b"test executable")
            firefox = Path(folder) / "firefox.exe"

            with (
                patch.object(minder_doctor, "_module_available", return_value=True),
                patch.object(minder_doctor, "_playwright_browser_paths",
                             return_value=({"Chromium": chromium, "Firefox": firefox}, "")),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    minder_doctor._browser_report()

        report = output.getvalue()
        self.assertIn("[OK] Browser runtime: Chromium", report)
        self.assertIn("[WARN] Browser runtime: Firefox", report)
        self.assertIn("python -m playwright install firefox", report)

    def test_browser_report_warns_when_local_driver_cannot_start(self):
        with (
            patch.object(minder_doctor, "_module_available", return_value=True),
            patch.object(minder_doctor, "_playwright_browser_paths",
                         return_value=({}, "RuntimeError: driver unavailable")),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                minder_doctor._browser_report()

        self.assertIn("could not inspect the local driver", output.getvalue())


if __name__ == "__main__":
    unittest.main()
