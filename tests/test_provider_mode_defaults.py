"""Regression tests for MINDER's provider-engine selection defaults.

These tests intentionally use only the Python standard library. They isolate the
configuration file in a temporary directory and never read or modify the user's
real config/api_keys.json.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from memory import config_manager


class ProviderModeDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self._old_config_dir = config_manager.CONFIG_DIR
        self._old_config_file = config_manager.CONFIG_FILE
        config_manager.CONFIG_DIR = Path(self._temporary.name) / "config"
        config_manager.CONFIG_FILE = config_manager.CONFIG_DIR / "api_keys.json"

    def tearDown(self) -> None:
        config_manager.CONFIG_DIR = self._old_config_dir
        config_manager.CONFIG_FILE = self._old_config_file
        self._temporary.cleanup()

    def test_fresh_install_defaults_to_provider_pipeline(self) -> None:
        self.assertFalse(config_manager.CONFIG_FILE.exists())
        self.assertEqual(config_manager.get_provider_mode(), "pipeline")
        self.assertEqual(config_manager.get_pipeline(), {})

    def test_gemini_requires_an_explicit_choice(self) -> None:
        self.assertEqual(config_manager.get_provider_mode(), "pipeline")
        config_manager.save_provider_mode("gemini")
        self.assertEqual(config_manager.get_provider_mode(), "gemini")

    def test_invalid_saved_mode_fails_closed_to_pipeline(self) -> None:
        config_manager.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_manager.CONFIG_FILE.write_text(
            json.dumps({"providers": {"mode": "unknown-engine"}}),
            encoding="utf-8",
        )
        self.assertEqual(config_manager.get_provider_mode(), "pipeline")

    def test_invalid_mode_write_is_normalised_to_pipeline(self) -> None:
        config_manager.save_provider_mode("not-a-provider")
        self.assertEqual(config_manager.get_provider_mode(), "pipeline")

    def test_provider_settings_do_not_implicitly_enable_gemini(self) -> None:
        config_manager.save_provider_config(
            "openai_compat@groq",
            {"base_url": "https://api.groq.com/openai/v1", "model": "test-model"},
        )
        config_manager.save_pipeline(llm="openai_compat@groq")
        self.assertEqual(config_manager.get_provider_mode(), "pipeline")
        self.assertEqual(
            config_manager.get_pipeline().get("llm"),
            "openai_compat@groq",
        )

    def test_updates_preserve_unrelated_user_configuration(self) -> None:
        config_manager.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_manager.CONFIG_FILE.write_text(
            json.dumps({"assistant_name": "MINDER", "user_name": "local-user"}),
            encoding="utf-8",
        )
        config_manager.save_provider_mode("pipeline")
        saved = json.loads(config_manager.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["assistant_name"], "MINDER")
        self.assertEqual(saved["user_name"], "local-user")
        self.assertEqual(saved["providers"]["mode"], "pipeline")


if __name__ == "__main__":
    unittest.main()
