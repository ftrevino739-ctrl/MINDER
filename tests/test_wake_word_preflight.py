"""Tests for the read-only wake-word training preflight."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "wake_word_training" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("minder_wake_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WakeWordPreflightTests(unittest.TestCase):
    def test_missing_training_root_fails_without_side_effects(self):
        missing = Path("this-training-root-does-not-exist-for-test")
        errors, warnings = MODULE.inspect_workspace(
            missing, missing / "hey_minder.yml", 20.0
        )
        self.assertTrue(errors)
        self.assertIn("Training root does not exist", errors[0])
        self.assertEqual(warnings, [])

    def test_relative_paths_resolve_from_training_root(self):
        root = Path("training-workspace")
        self.assertEqual(
            MODULE.resolve(root, "./assets/rirs"),
            root / "assets" / "rirs",
        )

    def test_required_config_includes_upstream_training_inputs(self):
        self.assertIn("feature_data_files", MODULE.REQUIRED_CONFIG)
        self.assertIn("false_positive_validation_data_path", MODULE.REQUIRED_CONFIG)
        self.assertIn("piper_sample_generator_path", MODULE.REQUIRED_CONFIG)


if __name__ == "__main__":
    unittest.main()
