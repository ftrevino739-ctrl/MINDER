"""Tests for the read-only wake-word training preflight."""
import importlib.util
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    def test_rejects_nested_only_assets_hidden_from_upstream_scandir(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "openwakeword").mkdir()
            (root / "openwakeword" / "train.py").touch()
            (root / "vendor" / "piper-sample-generator").mkdir(parents=True)
            (root / "vendor" / "piper-sample-generator" / "generate_samples.py").touch()
            (root / "assets" / "rirs" / "nested").mkdir(parents=True)
            (root / "assets" / "rirs" / "nested" / "room.wav").touch()
            (root / "assets" / "background").mkdir(parents=True)
            (root / "assets" / "background" / "noise.wav").touch()
            (root / "assets" / "validation.npy").touch()
            (root / "assets" / "negative.npy").touch()
            (root / "artifacts").mkdir()
            config_path = root / "hey_minder.yml"
            config_path.touch()
            config = {
                "model_name": "hey_minder", "target_phrase": ["hey minder"],
                "custom_negative_phrases": ["hey reminder"], "n_samples": 20000,
                "n_samples_val": 2000, "tts_batch_size": 25,
                "augmentation_batch_size": 16, "augmentation_rounds": 1,
                "piper_sample_generator_path": "./vendor/piper-sample-generator",
                "output_dir": "./artifacts/hey_minder",
                "rir_paths": ["./assets/rirs"],
                "background_paths": ["./assets/background"],
                "background_paths_duplication_rate": [1],
                "false_positive_validation_data_path": "./assets/validation.npy",
                "feature_data_files": {"ACAV100M_sample": "./assets/negative.npy"},
                "batch_n_per_class": {"ACAV100M_sample": 1024,
                                      "adversarial_negative": 50, "positive": 50},
                "model_type": "dnn", "layer_size": 32, "steps": 50000,
                "max_negative_weight": 1500, "target_false_positives_per_hour": 0.2,
            }
            with patch.object(MODULE, "load_config", return_value=config), \
                 patch.object(MODULE.importlib.util, "find_spec", return_value=object()):
                errors, _ = MODULE.inspect_workspace(root, config_path, 0)
        self.assertTrue(any("only top-level files" in error for error in errors))
        self.assertTrue(any("Default Piper LibriTTS checkpoint is missing" in error
                            for error in errors))
        self.assertTrue(any("melspectrogram.onnx" in error for error in errors))
        self.assertTrue(any("embedding_model.onnx" in error for error in errors))

    def test_preflight_covers_upstream_training_imports(self):
        for module in ("torchinfo", "torchmetrics", "pronouncing",
                       "audiomentations", "torch_audiomentations",
                       "speechbrain", "torchaudio", "mutagen",
                       "acoustics", "webrtcvad", "espeak_phonemizer"):
            self.assertIn(module, MODULE.REQUIRED_TRAINING_MODULES)

    def test_dependency_runtime_rejects_mismatched_torch_pair(self):
        versions = {"torch": "2.8.0+cpu", "torchaudio": "2.7.1+cpu"}
        with patch.object(MODULE.importlib.util, "find_spec", return_value=object()), \
             patch.object(MODULE.importlib.metadata, "version",
                          side_effect=lambda name: versions[name]):
            errors = MODULE.inspect_dependency_runtime(modules=("torch", "torchaudio"))
        self.assertTrue(any("versions must match exactly" in error for error in errors))

    def test_dependency_runtime_accepts_matching_torch_pair(self):
        versions = {"torch": "2.8.0+cpu", "torchaudio": "2.8.0+cpu"}
        with patch.object(MODULE.importlib.util, "find_spec", return_value=object()), \
             patch.object(MODULE.importlib.metadata, "version",
                          side_effect=lambda name: versions[name]):
            errors = MODULE.inspect_dependency_runtime(modules=("torch", "torchaudio"))
        self.assertEqual(errors, [])

    def test_optional_import_check_reports_binary_load_failure(self):
        with patch.object(MODULE.importlib.util, "find_spec", return_value=object()), \
             patch.object(MODULE.importlib, "import_module",
                          side_effect=OSError("DLL load failed")), \
             patch.object(MODULE.importlib.metadata, "version",
                          side_effect=MODULE.importlib.metadata.PackageNotFoundError):
            errors = MODULE.inspect_dependency_runtime(
                verify_imports=True, modules=("torchaudio",)
            )
        self.assertTrue(any("cannot be imported: torchaudio" in error
                            and "DLL load failed" in error for error in errors))

    def test_rejects_invalid_batch_sizes_and_background_duplication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / "config.yml"
            config_path.touch()
            config = {
                "model_name": "hey_minder", "target_phrase": ["hey minder"],
                "n_samples": 20000, "feature_data_files": {"ACAV100M_sample": "neg.npy"},
                "batch_n_per_class": {"ACAV100M_sample": 0, "positive": 50},
                "background_paths": ["bg"], "background_paths_duplication_rate": [1, 2],
            }
            with patch.object(MODULE, "load_config", return_value=config):
                # Stop after config validation by omitting the upstream trainer;
                # the assertions target the malformed values, not missing files.
                errors, _ = MODULE.inspect_workspace(root, config_path, 0)
        self.assertTrue(any("adversarial_negative" in error for error in errors))
        self.assertTrue(any("positive integer" in error for error in errors))
        self.assertTrue(any("must match background_paths length" in error for error in errors))

if __name__ == "__main__":
    unittest.main()
