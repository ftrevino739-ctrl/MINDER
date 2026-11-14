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


if __name__ == "__main__":
    unittest.main()
