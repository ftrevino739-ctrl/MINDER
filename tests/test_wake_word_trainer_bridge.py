"""Tests for MINDER's explicit Windows-safe training bridge."""
import hashlib
import importlib.util
import json
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.wake_word_training import trainer_bridge as BRIDGE


def _project_config():
    return {
        "model_name": "hey_minder",
        "target_phrase": ["hey minder"],
        "custom_negative_phrases": ["hey reminder", "hey mentor", "hey render"],
        "n_samples": 20000,
        "n_samples_val": 2000,
        "tts_batch_size": 25,
        "augmentation_batch_size": 16,
        "augmentation_rounds": 1,
        "output_dir": "artifacts/hey_minder",
        "false_positive_validation_data_path": "assets/validation.npy",
        "feature_data_files": {
            "ACAV100M_sample": "assets/acav.npy",
        },
        "batch_n_per_class": {
            "ACAV100M_sample": 1024,
            "adversarial_negative": 50,
            "positive": 50,
        },
        "model_type": "dnn",
        "layer_size": 32,
        "steps": 50000,
        "max_negative_weight": 1500,
        "target_false_positives_per_hour": 0.2,
    }


class FakeArray:
    def __init__(self, shape, payload=None, dtype="float32"):
        self.shape = tuple(shape)
        self.ndim = len(self.shape)
        self.payload = payload
        self.dtype = dtype

    def __len__(self):
        return self.shape[0]


class FakeNumpy:
    float32 = "float32"
    int16 = "int16"

    def __init__(self, path_shapes):
        self.path_shapes = path_shapes
        self.loaded = []

    def load(self, path, mmap_mode=None, allow_pickle=False):
        self.loaded.append((Path(path).name, mmap_mode, allow_pickle))
        return FakeArray(self.path_shapes[Path(path).name])

    def concatenate(self, arrays, axis=0):
        shape = list(arrays[0].shape)
        shape[axis] = sum(array.shape[axis] for array in arrays)
        return FakeArray(shape)

    def ones(self, count, dtype=None):
        return FakeArray((count,), dtype=dtype or self.float32)

    def zeros(self, count, dtype=None):
        return FakeArray((count,), dtype=dtype or self.float32)

    def asarray(self, array, dtype=None):
        return array

    def vstack(self, values):
        rows = []
        for value in values:
            rows.extend(value)
        width = len(rows[0]) if rows else 0
        return FakeArray((len(rows), width), payload=rows)

    def stack(self, values):
        return FakeArray((len(values),) + tuple(values[0].shape), payload=values)


class FakeTensor:
    def __init__(self, value):
        self.value = value
        self.shape = value.shape


class FakeDataLoader:
    def __init__(self, dataset, batch_size=None, num_workers=None):
        self.dataset = dataset
        self.batch_size = batch_size
        self.num_workers = num_workers

    def __iter__(self):
        if hasattr(self.dataset, "__iter__"):
            return iter(self.dataset)
        return iter(())


class FakeTensorDataset:
    def __init__(self, *tensors):
        self.tensors = tensors


class FakeIterableDataset:
    pass


class FakeTorch:
    def __init__(self):
        self.utils = type("Utils", (), {})()
        self.utils.data = type("Data", (), {})()
        self.utils.data.IterableDataset = FakeIterableDataset
        self.utils.data.DataLoader = FakeDataLoader
        self.utils.data.TensorDataset = FakeTensorDataset

    @staticmethod
    def from_numpy(value):
        return FakeTensor(value)


class FakeModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _approved_manifest(root, relative_paths):
    assets = []
    for index, relative in enumerate(relative_paths):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic fixture " + str(index)).encode("utf-8"))
        assets.append({
            "asset_id": "asset-" + str(index),
            "asset_type": "synthetic_test_fixture",
            "source_url": "https://example.test/assets/" + str(index),
            "source_revision": "test-fixture-r1",
            "relative_path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license_id": "CC0-1.0",
            "attribution": "Synthetic test fixture created for unit testing.",
            "restrictions": [],
            "review_status": "approved",
            "reviewed_by": "unit-test",
            "reviewed_at": "2026-11-21",
            "approved_uses": ["model_training"],
            "derived_from": [],
        })
    manifest = root / "asset_manifest.json"
    manifest.write_text(json.dumps({"manifest_version": 1, "assets": assets}), encoding="utf-8")
    return manifest


class TrainerBridgeTests(unittest.TestCase):
    def test_checkout_gate_requires_the_reviewed_upstream_commit(self):
        class Result:
            def __init__(self, output, returncode=0):
                self.stdout = output
                self.stderr = ""
                self.returncode = returncode

        with patch.object(BRIDGE.subprocess, "run",
                          return_value=Result(BRIDGE.PINNED_OPENWAKEWORD_COMMIT + "\\n")):
            self.assertEqual(
                BRIDGE.verify_pinned_openwakeword_checkout(Path(".")),
                BRIDGE.PINNED_OPENWAKEWORD_COMMIT,
            )
        with patch.object(BRIDGE.subprocess, "run",
                          return_value=Result("deadbeef\\n")):
            with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "only supports the reviewed"):
                BRIDGE.verify_pinned_openwakeword_checkout(Path("."))

    def test_accepts_current_minder_training_profile(self):
        BRIDGE.validate_minder_config(_project_config())

    def test_rejects_a_different_or_additional_target_phrase(self):
        config = _project_config()
        config["target_phrase"] = ["hey jarvis"]
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "product phrase"):
            BRIDGE.validate_minder_config(config)

        config["target_phrase"] = ["hey minder", "hey jarvis"]
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "only the product phrase"):
            BRIDGE.validate_minder_config(config)

    def test_rejects_small_or_boolean_sample_counts(self):
        config = _project_config()
        config["n_samples"] = 1000
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "20,000"):
            BRIDGE.validate_minder_config(config)

        config = _project_config()
        config["n_samples_val"] = True
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "2,000"):
            BRIDGE.validate_minder_config(config)

    def test_rejects_missing_negative_dataset_batch_size(self):
        config = _project_config()
        del config["batch_n_per_class"]["ACAV100M_sample"]
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "ACAV100M_sample"):
            BRIDGE.validate_minder_config(config)

    def test_resolves_relative_paths_from_selected_training_root(self):
        root = Path("training-workspace")
        self.assertEqual(
            BRIDGE.resolve_training_path(root, "./assets/validation.npy"),
            (root / "assets/validation.npy").resolve(),
        )

    def test_negative_feature_reshape_matches_pinned_non_overlapping_upstream_windows(self):
        class Matrix:
            def __init__(self, rows):
                self.rows = rows
                self.shape = (len(rows), len(rows[0]) if rows else 0)

            def __iter__(self):
                return iter(self.rows)

            def __getitem__(self, item):
                if isinstance(item, tuple):
                    row_slice, column_slice = item
                    return [row[column_slice] for row in self.rows[row_slice]]
                return self.rows[item]

        class Batch:
            def __init__(self, items):
                self.items = items
                self.shape = (len(items), len(items[0]), len(items[0][0]))

            def __iter__(self):
                return iter(self.items)

        class Numpy:
            float32 = "float32"

            @staticmethod
            def vstack(values):
                return Matrix([row for item in values for row in item])

            @staticmethod
            def stack(values):
                return Batch(values)

            @staticmethod
            def empty(shape, dtype=None):
                return type("Empty", (), {"shape": shape, "dtype": dtype})()

        source = Batch([
            [[i, i + 100] for i in range(5)],
            [[i + 5, i + 105] for i in range(5)],
        ])
        reshaped = BRIDGE.reshape_negative_feature_batch(source, 4, Numpy)
        self.assertEqual(reshaped.shape, (2, 4, 2))
        self.assertEqual(reshaped.items[0], [[0, 100], [1, 101], [2, 102], [3, 103]])
        self.assertEqual(reshaped.items[1], [[4, 104], [5, 105], [6, 106], [7, 107]])

    def test_negative_feature_reshape_preserves_already_matching_windows(self):
        source = FakeArray((4, 16, 96))
        result = BRIDGE.reshape_negative_feature_batch(source, 16, FakeNumpy({}))
        self.assertIs(result, source)

    def test_manifest_must_cover_every_feature_file_used_by_training(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = _project_config()
            output = root / "artifacts" / "hey_minder"
            feature_dir = output / "hey_minder"
            relative = [
                "assets/validation.npy",
                "assets/acav.npy",
                "artifacts/hey_minder/hey_minder/positive_features_train.npy",
                "artifacts/hey_minder/hey_minder/negative_features_train.npy",
                "artifacts/hey_minder/hey_minder/positive_features_test.npy",
                "artifacts/hey_minder/hey_minder/negative_features_test.npy",
            ]
            manifest = _approved_manifest(root, relative)
            required, _ = BRIDGE.validate_training_workspace(config, root, manifest)
            self.assertEqual(len(required), 6)

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["assets"] = payload["assets"][:-1]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "not declared"):
                BRIDGE.validate_training_workspace(config, root, manifest)

    def test_manifest_gate_rejects_unapproved_asset_before_training(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = _project_config()
            relative = [
                "assets/validation.npy",
                "assets/acav.npy",
                "artifacts/hey_minder/hey_minder/positive_features_train.npy",
                "artifacts/hey_minder/hey_minder/negative_features_train.npy",
                "artifacts/hey_minder/hey_minder/positive_features_test.npy",
                "artifacts/hey_minder/hey_minder/negative_features_test.npy",
            ]
            manifest = _approved_manifest(root, relative)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["assets"][1]["review_status"] = "pending"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "manifest rejected"):
                BRIDGE.validate_training_workspace(config, root, manifest)

    def test_feature_shape_gate_rejects_mismatched_test_arrays(self):
        arrays = {
            "positive_features_train": FakeArray((10, 16, 96)),
            "negative_features_train": FakeArray((10, 16, 96)),
            "positive_features_test": FakeArray((2, 16, 96)),
            "negative_features_test": FakeArray((2, 12, 96)),
            "false_positive_validation": FakeArray((100, 96)),
        }
        with self.assertRaisesRegex(BRIDGE.TrainingBridgeError, "shapes do not match"):
            BRIDGE.validate_feature_shapes(arrays)

    def test_export_flow_loads_the_same_openwakeword_onnx_runtime_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            output_dir = Path(folder)

            class TrainingModel:
                def __init__(self):
                    self.train_args = None
                    self.export_args = None

                def auto_train(self, **kwargs):
                    self.train_args = kwargs
                    return "best-model"

                def export_model(self, **kwargs):
                    self.export_args = kwargs
                    (output_dir / "hey_minder.onnx").write_bytes(b"synthetic onnx test file")

            class RuntimeModel:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

                def predict(self, audio):
                    return {"hey_minder": 0.0}

            training_model = TrainingModel()
            runtime_module = types.ModuleType("openwakeword.model")
            runtime_module.Model = RuntimeModel
            package_module = types.ModuleType("openwakeword")
            package_module.__path__ = []

            bundle = BRIDGE.TrainingBundle(
                model=training_model,
                training_loader=object(),
                validation_loader=object(),
                false_positive_loader=object(),
                output_dir=output_dir,
                output_path=output_dir / "hey_minder.onnx",
                config={
                    "steps": 50000,
                    "max_negative_weight": 1500,
                    "target_false_positives_per_hour": 0.2,
                },
            )
            numpy_stub = types.SimpleNamespace(
                int16="int16",
                zeros=lambda count, dtype=None: (count, dtype),
            )
            with patch.dict(
                "sys.modules",
                {"openwakeword": package_module, "openwakeword.model": runtime_module},
            ):
                result = BRIDGE.train_and_export(bundle, np_module=numpy_stub)

            self.assertEqual(result, output_dir / "hey_minder.onnx")
            self.assertEqual(training_model.train_args["steps"], 50000)
            self.assertEqual(training_model.export_args["model"], "best-model")
            self.assertTrue((output_dir / "hey_minder.onnx").is_file())

    def test_build_bundle_uses_zero_workers_and_reiterable_mmap_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = _project_config()
            relative = [
                "assets/validation.npy",
                "assets/acav.npy",
                "artifacts/hey_minder/hey_minder/positive_features_train.npy",
                "artifacts/hey_minder/hey_minder/negative_features_train.npy",
                "artifacts/hey_minder/hey_minder/positive_features_test.npy",
                "artifacts/hey_minder/hey_minder/negative_features_test.npy",
            ]
            manifest = _approved_manifest(root, relative)
            path_shapes = {
                "positive_features_train.npy": (8, 16, 96),
                "negative_features_train.npy": (8, 16, 96),
                "positive_features_test.npy": (4, 16, 96),
                "negative_features_test.npy": (4, 16, 96),
                "validation.npy": (200, 96),
                "acav.npy": (1000, 76, 96),
            }
            numpy = FakeNumpy(path_shapes)
            torch = FakeTorch()
            mmap_calls = []

            class MmapStream:
                def __iter__(self):
                    return self

                def __next__(self):
                    return FakeArray((50, 16, 96)), FakeArray((50,))

            def mmap_factory(files, **kwargs):
                mmap_calls.append((files, kwargs))
                return MmapStream()

            dependencies = (numpy, torch, mmap_factory, FakeModel)
            bundle = BRIDGE.build_training_bundle(
                config,
                root,
                manifest,
                validation_batch_size=256,
                dependencies=dependencies,
                require_pinned_checkout=False,
            )

            self.assertEqual(bundle.model.kwargs["input_shape"], (16, 96))
            self.assertEqual(bundle.training_loader.num_workers, 0)
            self.assertEqual(bundle.training_loader.batch_size, None)
            self.assertEqual(bundle.validation_loader.num_workers, 0)
            self.assertEqual(len(bundle.false_positive_loader), 1)
            self.assertTrue(bundle.output_path.name == "hey_minder.onnx")

            # Asking for two passes must construct two independent mmap iterators.
            first = iter(bundle.training_loader.dataset)
            second = iter(bundle.training_loader.dataset)
            self.assertTrue(hasattr(first, "__next__"))
            self.assertTrue(hasattr(second, "__next__"))
            next(first)
            next(second)
            self.assertEqual(len(mmap_calls), 2)
            self.assertEqual(
                set(mmap_calls[0][0]),
                {"ACAV100M_sample", "positive", "adversarial_negative"},
            )
            self.assertEqual(mmap_calls[0][1]["n_per_class"], config["batch_n_per_class"])


if __name__ == "__main__":
    unittest.main()
