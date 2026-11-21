"""Tests for MINDER's manifest-gated audio augmentation and feature preparation."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.wake_word_training import prepare_features as PREP


class FakeNumpy:
    def __init__(self):
        self.random = SimpleNamespace(seed=lambda seed: None)


class FeaturePreparationTests(unittest.TestCase):
    def test_choose_clip_length_uses_upstream_median_plus_750ms_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = []
            for index, frames in enumerate((40000, 50000)):
                path = root / f"positive_{index}.wav"
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b"\x00\x00" * frames)
                paths.append(path)

            result_a = PREP._choose_clip_length(paths, seed=77)
            result_b = PREP._choose_clip_length(paths, seed=77)
            self.assertEqual(result_a, result_b)
            self.assertIn(result_a, (52000, 62000))

    def test_approved_flat_audio_files_requires_manifested_asset_type(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "assets" / "rirs"
            directory.mkdir(parents=True)
            files = [directory / "room_a.wav", directory / "room_b.wav"]
            for path in files:
                path.write_bytes(b"rir")
            index = {
                "rir-a": {
                    "asset_id": "rir-a",
                    "relative_path": "assets/rirs/room_a.wav",
                    "asset_type": "room_impulse_response",
                },
                "rir-b": {
                    "asset_id": "rir-b",
                    "relative_path": "assets/rirs/room_b.wav",
                    "asset_type": "room_impulse_response",
                },
            }

            selected, ids = PREP._approved_flat_audio_files(
                ["assets/rirs"],
                training_root=root,
                asset_root=root,
                asset_index=index,
                expected_asset_type="room_impulse_response",
                label="RIR",
            )
            self.assertEqual(selected, [path.resolve() for path in files])
            self.assertEqual(ids, ["rir-a", "rir-b"])

            index["rir-b"]["asset_type"] = "background_audio"
            with self.assertRaisesRegex(PREP.FeaturePreparationError, "asset_type"):
                PREP._approved_flat_audio_files(
                    ["assets/rirs"],
                    training_root=root,
                    asset_root=root,
                    asset_index=index,
                    expected_asset_type="room_impulse_response",
                    label="RIR",
                )

    def test_rejects_nested_or_unregistered_augmentation_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "assets" / "background"
            (directory / "subdir").mkdir(parents=True)
            (directory / "subdir" / "noise.wav").write_bytes(b"noise")
            with self.assertRaisesRegex(PREP.FeaturePreparationError, "top-level files"):
                PREP._approved_flat_audio_files(
                    ["assets/background"],
                    training_root=root,
                    asset_root=root,
                    asset_index={},
                    expected_asset_type="background_audio",
                    label="background",
                )

            directory.rmdir()
            (directory / "noise.wav").write_bytes(b"noise")
            with self.assertRaisesRegex(PREP.FeaturePreparationError, "unique reviewed asset"):
                PREP._approved_flat_audio_files(
                    ["assets/background"],
                    training_root=root,
                    asset_root=root,
                    asset_index={},
                    expected_asset_type="background_audio",
                    label="background",
                )

    def _bundle(self, root):
        feature_dir = root / "artifacts" / "hey_minder" / "hey_minder"
        feature_dir.mkdir(parents=True)
        corpora = {
            "positive_train": [root / "corpus" / "positive_train" / "a.wav"],
            "positive_test": [root / "corpus" / "positive_test" / "b.wav"],
            "negative_train": [root / "corpus" / "negative_train" / "c.wav"],
            "negative_test": [root / "corpus" / "negative_test" / "d.wav"],
        }
        paths = {
            "positive_features_train": feature_dir / "positive_features_train.npy",
            "positive_features_test": feature_dir / "positive_features_test.npy",
            "negative_features_train": feature_dir / "negative_features_train.npy",
            "negative_features_test": feature_dir / "negative_features_test.npy",
        }
        return PREP.FeaturePreparationBundle(
            training_root=root,
            asset_root=root,
            feature_dir=feature_dir,
            corpora=corpora,
            corpus_asset_ids={split: split + "-asset" for split in corpora},
            room_impulse_responses=[root / "rirs" / "room.wav"],
            background_clips=[root / "background" / "music.wav"],
            augmentation_rounds=2,
            augmentation_batch_size=4,
            clip_duration_samples=32000,
            output_paths=paths,
            source_asset_ids=["positive-train-asset", "rir-asset", "background-asset"],
        )

    def test_execution_streams_all_four_splits_and_creates_pending_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = self._bundle(root)
            calls = []

            class FakeAudioFeatures:
                def __init__(self, device):
                    self.device = device

            def fake_augment(clips, **kwargs):
                calls.append((list(clips), kwargs))
                return ["synthetic-batch"]

            def fake_writer(generator, **kwargs):
                path = kwargs["output_path"]
                path.write_bytes(("feature:" + path.name).encode("utf-8"))

            with patch.object(PREP, "write_features_from_generator", side_effect=fake_writer),                  patch.object(PREP, "current_minder_source_revision", return_value="a" * 40):
                outputs, proposal_path = PREP.execute_feature_preparation(
                    {"augmentation_rounds": 2},
                    bundle,
                    seed=12,
                    dependencies=(FakeNumpy(), fake_augment, FakeAudioFeatures),
                )

            self.assertEqual(len(calls), 4)
            self.assertEqual(len(outputs), 4)
            for clip_paths, kwargs in calls:
                self.assertEqual(len(clip_paths), 2)
                self.assertEqual(kwargs["total_length"] if "total_length" in kwargs else bundle.clip_duration_samples, 32000)
                self.assertEqual(kwargs["batch_size"], 4)

            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["note"], "PENDING human review. These are not approved asset records.")
            self.assertEqual(len(payload["assets"]), 4)
            for asset in payload["assets"]:
                self.assertEqual(asset["review_status"], "pending")
                self.assertEqual(asset["approved_uses"], [])
                self.assertEqual(asset["derived_from"], bundle.source_asset_ids)
                actual = hashlib.sha256((root / asset["relative_path"]).read_bytes()).hexdigest()
                self.assertEqual(asset["sha256"], actual)

    def test_failure_rolls_back_feature_outputs_created_by_this_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = self._bundle(root)
            counter = {"writes": 0}

            class FakeAudioFeatures:
                def __init__(self, device):
                    pass

            def fake_writer(generator, **kwargs):
                counter["writes"] += 1
                kwargs["output_path"].write_bytes(b"new features")
                if counter["writes"] == 2:
                    raise RuntimeError("synthetic test failure")

            with patch.object(PREP, "write_features_from_generator", side_effect=fake_writer):
                with self.assertRaisesRegex(PREP.FeaturePreparationError, "synthetic test failure"):
                    PREP.execute_feature_preparation(
                        {"augmentation_rounds": 1},
                        bundle,
                        seed=5,
                        dependencies=(FakeNumpy(), lambda *args, **kwargs: [], FakeAudioFeatures),
                    )

            self.assertFalse(any(path.exists() for path in bundle.output_paths.values()))

    def test_training_preparation_cli_requires_both_heavy_operation_confirmations(self):
        common = [
            "--training-root", "unused",
            "--asset-root", "unused",
            "--config", "unused.yml",
            "--asset-manifest", "unused.json",
        ]
        for split in ("positive-train", "positive-test", "negative-train", "negative-test"):
            common.extend([
                f"--{split}-corpus-manifest", "unused-corpus.json",
                f"--{split}-corpus-asset-id", split + "-asset",
            ])
        with self.assertRaises(SystemExit) as missing_confirmation:
            PREP.main(common + ["--execute"])
        self.assertEqual(missing_confirmation.exception.code, 2)

        with self.assertRaises(SystemExit) as confirmation_without_execute:
            PREP.main(common + ["--confirm-heavy-preparation"])
        self.assertEqual(confirmation_without_execute.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
