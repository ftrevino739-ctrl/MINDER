"""MINDER-owned bridge from approved feature assets to openWakeWord training.

This module keeps the product's actual wake phrase and ONNX runtime contract
as the source of truth. It does not download data, synthesize audio, record
microphone input, or start training unless the explicit CLI confirmation
flags are supplied.

The trainer uses openWakeWord's pinned Model and mmap_batch_generator
implementations; MINDER owns the Windows-safe, re-iterable data orchestration.
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


FEATURE_DIMENSIONS = 96
EXPECTED_MODEL_NAME = "hey_minder"
PINNED_OPENWAKEWORD_COMMIT = "368c03716d1e92591906a84949bc477f3a834455"
DEFAULT_VALIDATION_BATCH_SIZE = 1024
MAX_VALIDATION_BATCH_SIZE = 4096

_REQUIRED_CONFIG_FIELDS = {
    "model_name",
    "target_phrase",
    "custom_negative_phrases",
    "n_samples",
    "n_samples_val",
    "tts_batch_size",
    "augmentation_batch_size",
    "augmentation_rounds",
    "output_dir",
    "false_positive_validation_data_path",
    "feature_data_files",
    "batch_n_per_class",
    "model_type",
    "layer_size",
    "steps",
    "max_negative_weight",
    "target_false_positives_per_hour",
}


class TrainingBridgeError(ValueError):
    """Raised when the MINDER training inputs do not meet the bridge contract."""


@dataclass
class TrainingBundle:
    """Prepared, re-iterable streams consumed by openWakeWord Model.auto_train."""

    model: Any
    training_loader: Any
    validation_loader: Any
    false_positive_loader: Any
    output_dir: Path
    output_path: Path
    config: Mapping[str, Any]


def resolve_training_path(training_root: Path, value: str | Path) -> Path:
    """Resolve a config path against the explicitly selected training root."""
    path = Path(value)
    return path.resolve() if path.is_absolute() else (training_root / path).resolve()


def verify_pinned_openwakeword_checkout(training_root: Path) -> str:
    """Require the exact upstream source revision reviewed by MINDER."""
    try:
        result = subprocess.run(
            ["git", "-C", str(training_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrainingBridgeError(
            f"Cannot verify openWakeWord Git checkout at {training_root}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git rev-parse failed").strip()
        raise TrainingBridgeError(
            f"Training root is not a readable Git checkout: {training_root} ({detail})"
        )
    actual = result.stdout.strip().lower()
    if actual != PINNED_OPENWAKEWORD_COMMIT:
        raise TrainingBridgeError(
            "The Windows trainer bridge only supports the reviewed openWakeWord "
            f"commit {PINNED_OPENWAKEWORD_COMMIT}; found {actual or \"unknown\"}."
        )
    return actual

def validate_minder_config(config: Mapping[str, Any]) -> None:
    """Reject configuration drift from the MINDER wake phrase/model contract."""
    missing = sorted(_REQUIRED_CONFIG_FIELDS - set(config))
    if missing:
        raise TrainingBridgeError("Missing MINDER training config keys: " + ", ".join(missing))

    if config.get("model_name") != EXPECTED_MODEL_NAME:
        raise TrainingBridgeError("model_name must be exactly 'hey_minder'.")

    phrases = config.get("target_phrase")
    normalized_phrases = (
        [str(value).strip().casefold() for value in phrases]
        if isinstance(phrases, list) else []
    )
    if normalized_phrases != ["hey minder"]:
        raise TrainingBridgeError(
            "target_phrase must contain only the product phrase 'hey minder'."
        )

    if not isinstance(config.get("custom_negative_phrases"), list):
        raise TrainingBridgeError("custom_negative_phrases must be a list.")
    negatives = [str(value).strip().casefold() for value in config["custom_negative_phrases"]]
    if not negatives or any(not value for value in negatives):
        raise TrainingBridgeError("custom_negative_phrases must contain non-empty hard negatives.")

    positive_count = config.get("n_samples")
    validation_count = config.get("n_samples_val")
    if (not isinstance(positive_count, int) or isinstance(positive_count, bool)
            or positive_count < 20000):
        raise TrainingBridgeError("n_samples must be an integer of at least 20,000.")
    if (not isinstance(validation_count, int) or isinstance(validation_count, bool)
            or validation_count < 2000):
        raise TrainingBridgeError("n_samples_val must be an integer of at least 2,000.")

    for key in ("tts_batch_size", "augmentation_batch_size", "augmentation_rounds",
                "steps", "layer_size", "max_negative_weight"):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TrainingBridgeError(f"{key} must be a positive integer.")

    if config.get("model_type") not in ("dnn", "rnn"):
        raise TrainingBridgeError("model_type must be 'dnn' or 'rnn'.")

    try:
        fp_target = float(config.get("target_false_positives_per_hour"))
    except (TypeError, ValueError) as exc:
        raise TrainingBridgeError(
            "target_false_positives_per_hour must be a positive number."
        ) from exc
    if not (0 < fp_target < float("inf")):
        raise TrainingBridgeError("target_false_positives_per_hour must be finite and positive.")

    feature_files = config.get("feature_data_files")
    if not isinstance(feature_files, dict) or not feature_files:
        raise TrainingBridgeError("feature_data_files must be a non-empty mapping.")
    if "ACAV100M_sample" not in feature_files:
        raise TrainingBridgeError("feature_data_files must include ACAV100M_sample.")

    batch_sizes = config.get("batch_n_per_class")
    if not isinstance(batch_sizes, dict):
        raise TrainingBridgeError("batch_n_per_class must be a mapping.")
    required_batches = set(feature_files) | {"positive", "adversarial_negative"}
    missing_batches = sorted(required_batches - set(batch_sizes))
    if missing_batches:
        raise TrainingBridgeError(
            "batch_n_per_class is missing: " + ", ".join(missing_batches)
        )
    for label, value in batch_sizes.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TrainingBridgeError(f"batch_n_per_class[{label!r}] must be a positive integer.")

    for key in ("output_dir", "false_positive_validation_data_path"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise TrainingBridgeError(f"{key} must be a non-empty path string.")
    for label, path in feature_files.items():
        if not isinstance(path, str) or not path.strip():
            raise TrainingBridgeError(f"feature_data_files[{label!r}] must be a path string.")


def reshape_negative_feature_batch(features: Any, window_size: int, np_module: Any) -> Any:
    """Mirror the pinned upstream negative-feature re-chunking rule.

    When an external negative feature file uses a different frame length,
    consecutive non-overlapping windows are created. As in the pinned
    upstream implementation, the final incomplete chunk and final possible
    exact-end chunk are excluded by range(0, rows - window_size, window_size).
    """
    shape = getattr(features, "shape", None)
    if shape is None or len(shape) != 3:
        raise TrainingBridgeError("Negative feature batches must be 3-D arrays.")
    if shape[1] == window_size:
        return features

    flattened = np_module.vstack(features)
    starts = range(0, flattened.shape[0] - window_size, window_size)
    windows = [flattened[index:index + window_size, :] for index in starts]
    if not windows:
        return np_module.empty(
            (0, window_size, flattened.shape[-1]),
            dtype=getattr(flattened, "dtype", np_module.float32),
        )
    return np_module.stack(windows)


def _required_feature_paths(config: Mapping[str, Any], training_root: Path) -> dict[str, Path]:
    """Return every .npy input consumed by the train stage."""
    output_dir = resolve_training_path(training_root, config["output_dir"])
    feature_dir = output_dir / EXPECTED_MODEL_NAME
    required = {
        "positive_features_train": feature_dir / "positive_features_train.npy",
        "negative_features_train": feature_dir / "negative_features_train.npy",
        "positive_features_test": feature_dir / "positive_features_test.npy",
        "negative_features_test": feature_dir / "negative_features_test.npy",
        "false_positive_validation": resolve_training_path(
            training_root, config["false_positive_validation_data_path"]
        ),
    }
    for label, value in config["feature_data_files"].items():
        required[f"external_negative:{label}"] = resolve_training_path(training_root, value)
    return required


def validate_manifest_coverage(
    manifest_path: Path,
    training_root: Path,
    required_paths: Mapping[str, Path],
) -> list[str]:
    """Require approved, hash-verified manifest entries for every train input."""
    try:
        from tools.wake_word_training.asset_manifest import validate_manifest
    except ImportError:
        # Allows direct execution from this directory as a script.
        from asset_manifest import validate_manifest

    try:
        asset_ids = validate_manifest(manifest_path, training_root, verify_hashes=True,
                                      required_use="model_training")
    except Exception as exc:
        raise TrainingBridgeError(f"Training asset manifest rejected: {exc}") from exc

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrainingBridgeError(f"Cannot read training asset manifest: {exc}") from exc

    declared = {
        asset["relative_path"]
        for asset in payload.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("relative_path"), str)
    }
    missing: list[str] = []
    root = training_root.resolve()
    for label, path in required_paths.items():
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            missing.append(f"{label}: path is outside training root ({path})")
            continue
        if relative not in declared:
            missing.append(f"{label}: not declared in asset manifest ({relative})")
        if not path.is_file():
            missing.append(f"{label}: file does not exist ({path})")

    if missing:
        raise TrainingBridgeError(
            "Training asset manifest does not cover all required inputs:\n- "
            + "\n- ".join(missing)
        )
    return asset_ids


def validate_feature_shapes(
    arrays: Mapping[str, Any],
    *,
    expected_feature_dimensions: int = FEATURE_DIMENSIONS,
) -> tuple[int, int]:
    """Check generated/test/false-positive arrays without loading huge data into RAM."""
    positive_test = arrays["positive_features_test"]
    negative_test = arrays["negative_features_test"]

    for name in ("positive_features_train", "negative_features_train",
                 "positive_features_test", "negative_features_test"):
        array = arrays[name]
        shape = getattr(array, "shape", None)
        if shape is None or len(shape) != 3:
            raise TrainingBridgeError(f"{name} must have shape (clips, frames, features).")
        if shape[0] <= 0 or shape[1] <= 0 or shape[2] != expected_feature_dimensions:
            raise TrainingBridgeError(
                f"{name} must contain non-empty {expected_feature_dimensions}-dimension features."
            )

    input_shape = tuple(positive_test.shape[1:])
    if tuple(negative_test.shape[1:]) != input_shape:
        raise TrainingBridgeError("Positive and negative test feature shapes do not match.")
    if tuple(arrays["positive_features_train"].shape[1:]) != input_shape:
        raise TrainingBridgeError("Positive train and test feature shapes do not match.")
    if tuple(arrays["negative_features_train"].shape[1:]) != input_shape:
        raise TrainingBridgeError("Negative train and test feature shapes do not match.")

    false_positive = arrays["false_positive_validation"]
    if getattr(false_positive, "ndim", None) != 2 or false_positive.shape[1] != expected_feature_dimensions:
        raise TrainingBridgeError(
            f"false-positive validation features must be 2-D with {expected_feature_dimensions} columns."
        )
    if false_positive.shape[0] <= input_shape[0]:
        raise TrainingBridgeError("False-positive validation stream is too short for one model window.")

    for label, feature_array in arrays.items():
        if label.startswith("external_negative:"):
            shape = getattr(feature_array, "shape", None)
            if shape is None or len(shape) != 3 or shape[0] <= 0 or shape[2] != expected_feature_dimensions:
                raise TrainingBridgeError(
                    f"{label} must be a non-empty 3-D feature array with "
                    f"{expected_feature_dimensions} columns."
                )

    return int(input_shape[0]), int(input_shape[1])


def validate_training_workspace(
    config: Mapping[str, Any],
    training_root: Path,
    manifest_path: Path,
    *,
    require_pinned_checkout: bool = False,
) -> tuple[dict[str, Path], list[str]]:
    """Read-only disk/config/manifest gate; never imports ML dependencies."""
    root = training_root.expanduser().resolve()
    if not root.is_dir():
        raise TrainingBridgeError(f"Training root does not exist: {root}")
    if require_pinned_checkout:
        verify_pinned_openwakeword_checkout(root)
    validate_minder_config(config)

    paths = _required_feature_paths(config, root)
    output_dir = resolve_training_path(root, config["output_dir"])
    if not output_dir.is_dir():
        raise TrainingBridgeError(f"Model output directory does not exist: {output_dir}")

    for label, path in paths.items():
        if not path.is_file():
            raise TrainingBridgeError(f"Required training asset is missing ({label}): {path}")

    asset_ids = validate_manifest_coverage(manifest_path.expanduser().resolve(), root, paths)
    return paths, asset_ids


class _TrainingBatchFactory:
    """Create a fresh pinned openWakeWord memmap stream for every epoch."""

    def __init__(
        self,
        mmap_batch_generator: Callable[..., Any],
        files: dict[str, str],
        batch_sizes: dict[str, int],
        data_transforms: dict[str, Callable[[Any], Any]],
        label_transforms: dict[str, Callable[[Any], list[int]]],
    ):
        self._mmap_batch_generator = mmap_batch_generator
        self._files = files
        self._batch_sizes = batch_sizes
        self._data_transforms = data_transforms
        self._label_transforms = label_transforms

    def __call__(self):
        return self._mmap_batch_generator(
            self._files,
            n_per_class=self._batch_sizes,
            data_transform_funcs=self._data_transforms,
            label_transform_funcs=self._label_transforms,
        )


def _positive_labels(values: Any) -> list[int]:
    return [1 for _ in values]


def _negative_labels(values: Any) -> list[int]:
    return [0 for _ in values]


def _load_training_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        import torch
        from openwakeword.data import mmap_batch_generator
        from openwakeword.train import Model
    except Exception as exc:
        raise TrainingBridgeError(
            "The isolated training environment could not import NumPy, PyTorch, "
            f"or the pinned openWakeWord trainer: {type(exc).__name__}: {exc}"
        ) from exc
    return np, torch, mmap_batch_generator, Model


def build_training_bundle(
    config: Mapping[str, Any],
    training_root: Path,
    manifest_path: Path,
    *,
    validation_batch_size: int = DEFAULT_VALIDATION_BATCH_SIZE,
    dependencies: tuple[Any, Any, Any, Any] | None = None,
    require_pinned_checkout: bool = True,
) -> TrainingBundle:
    """Build the Windows-safe, re-iterable train/validation streams.

    Importing this function does not load training packages. Dependencies can
    be injected for focused tests; real use imports the pinned stack lazily.
    """
    if (isinstance(validation_batch_size, bool)
            or not isinstance(validation_batch_size, int)
            or not 1 <= validation_batch_size <= MAX_VALIDATION_BATCH_SIZE):
        raise TrainingBridgeError(
            f"validation_batch_size must be between 1 and {MAX_VALIDATION_BATCH_SIZE}."
        )

    root = training_root.expanduser().resolve()
    paths, _asset_ids = validate_training_workspace(
        config, root, manifest_path, require_pinned_checkout=require_pinned_checkout
    )
    np, torch, mmap_batch_generator, Model = dependencies or _load_training_dependencies()

    arrays = {
        name: np.load(path, mmap_mode="r", allow_pickle=False)
        for name, path in paths.items()
    }
    window_size, feature_dimensions = validate_feature_shapes(arrays)

    input_shape = tuple(arrays["positive_features_test"].shape[1:])
    model = Model(
        n_classes=1,
        input_shape=input_shape,
        model_type=config["model_type"],
        layer_dim=config["layer_size"],
        seconds_per_example=1280 * input_shape[0] / 16000,
    )

    feature_files = {
        label: str(resolve_training_path(root, path))
        for label, path in config["feature_data_files"].items()
    }
    feature_files["positive"] = str(paths["positive_features_train"])
    feature_files["adversarial_negative"] = str(paths["negative_features_train"])

    def reshape_external_negatives(batch: Any) -> Any:
        return reshape_negative_feature_batch(batch, window_size, np)

    # The pinned upstream script only re-chunks external feature files.
    # The generated positive/adversarial-negative clips already have the
    # model's exact input window shape.
    data_transforms = {
        label: reshape_external_negatives
        for label in config["feature_data_files"]
    }
    label_transforms = {
        label: (_positive_labels if label == "positive" else _negative_labels)
        for label in feature_files
    }

    batch_factory = _TrainingBatchFactory(
        mmap_batch_generator,
        feature_files,
        dict(config["batch_n_per_class"]),
        data_transforms,
        label_transforms,
    )

    class ReiterableTrainingDataset(torch.utils.data.IterableDataset):
        """num_workers=0 avoids spawn/pickling and creates a new mmap stream per pass."""

        def __iter__(self):
            yield from batch_factory()

    training_loader = torch.utils.data.DataLoader(
        ReiterableTrainingDataset(),
        batch_size=None,
        num_workers=0,
    )

    positive_test = arrays["positive_features_test"]
    negative_test = arrays["negative_features_test"]
    validation_features = np.concatenate((positive_test, negative_test), axis=0)
    validation_labels = np.concatenate((
        np.ones(len(positive_test), dtype=np.float32),
        np.zeros(len(negative_test), dtype=np.float32),
    ))
    validation_features = np.asarray(validation_features, dtype=np.float32)
    validation_dataset = torch.utils.data.TensorDataset(
        torch.from_numpy(validation_features),
        torch.from_numpy(validation_labels),
    )
    validation_loader = torch.utils.data.DataLoader(
        validation_dataset,
        batch_size=len(validation_labels),
        num_workers=0,
    )

    from tools.wake_word_training.streaming_windows import TorchWindowBatches

    false_positive_loader = TorchWindowBatches(
        arrays["false_positive_validation"],
        window_size=window_size,
        batch_size=validation_batch_size,
    )

    output_dir = resolve_training_path(root, config["output_dir"])
    output_path = output_dir / f"{EXPECTED_MODEL_NAME}.onnx"

    return TrainingBundle(
        model=model,
        training_loader=training_loader,
        validation_loader=validation_loader,
        false_positive_loader=false_positive_loader,
        output_dir=output_dir,
        output_path=output_path,
        config=dict(config),
    )


def train_and_export(bundle: TrainingBundle, np_module: Any | None = None) -> Path:
    """Run the explicitly requested training and verify the exported ONNX model."""
    if not bundle.output_dir.is_dir():
        raise TrainingBridgeError(f"Output directory does not exist: {bundle.output_dir}")

    best_model = bundle.model.auto_train(
        X_train=bundle.training_loader,
        X_val=bundle.validation_loader,
        false_positive_val_data=bundle.false_positive_loader,
        steps=bundle.config["steps"],
        max_negative_weight=bundle.config["max_negative_weight"],
        target_fp_per_hour=bundle.config["target_false_positives_per_hour"],
    )
    bundle.model.export_model(
        model=best_model,
        model_name=EXPECTED_MODEL_NAME,
        output_dir=str(bundle.output_dir),
    )

    if not bundle.output_path.is_file() or bundle.output_path.stat().st_size <= 0:
        raise TrainingBridgeError(
            f"openWakeWord did not produce a non-empty ONNX model at {bundle.output_path}"
        )

    if np_module is None:
        try:
            import numpy as np_module
        except Exception as exc:
            raise TrainingBridgeError(f"Cannot load NumPy for exported-model validation: {exc}") from exc

    try:
        from openwakeword.model import Model as RuntimeModel
        runtime = RuntimeModel(
            wakeword_models=[str(bundle.output_path)],
            inference_framework="onnx",
        )
        output = runtime.predict(np_module.zeros(1280, dtype=np_module.int16))
    except Exception as exc:
        raise TrainingBridgeError(
            f"Exported ONNX model failed the openWakeWord runtime smoke test: {exc}"
        ) from exc

    if not isinstance(output, dict) or not any(
        EXPECTED_MODEL_NAME in str(key).casefold() for key in output
    ):
        raise TrainingBridgeError(
            "Exported ONNX runtime output does not expose the expected 'hey_minder' model key."
        )
    return bundle.output_path


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TrainingBridgeError(f"Cannot load MINDER YAML config: {exc}") from exc
    if not isinstance(config, dict):
        raise TrainingBridgeError("MINDER YAML config must contain a mapping.")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-root", type=Path, required=True,
                        help="Root directory containing the pinned openWakeWord checkout and training assets")
    parser.add_argument("--config", type=Path, required=True,
                        help="Path to MINDER's hey_minder.yml")
    parser.add_argument("--asset-manifest", type=Path, required=True,
                        help="Human-reviewed manifest covering every feature array used by training")
    parser.add_argument("--validation-batch-size", type=int, default=DEFAULT_VALIDATION_BATCH_SIZE)
    parser.add_argument("--execute", action="store_true",
                        help="Actually run the 50k-step model training job; otherwise perform a read-only gate")
    parser.add_argument("--confirm-heavy-training", action="store_true",
                        help="Required together with --execute to authorize CPU, RAM, disk and time intensive training")
    args = parser.parse_args(argv)

    if args.execute and not args.confirm_heavy_training:
        parser.error("--execute requires --confirm-heavy-training.")
    if args.confirm_heavy_training and not args.execute:
        parser.error("--confirm-heavy-training only applies with --execute.")

    try:
        config = _load_yaml_config(args.config.expanduser().resolve())
        paths, asset_ids = validate_training_workspace(
            config,
            args.training_root,
            args.asset_manifest,
            require_pinned_checkout=True,
        )
        print("MINDER training workspace PASSED.")
        print(f"Reviewed asset records: {len(asset_ids)}")
        print(f"Required feature files: {len(paths)}")
        for label, path in paths.items():
            print(f"  {label}: {path}")
        print("No assets were downloaded or changed.")

        if not args.execute:
            print("Read-only mode: training was NOT started.")
            return 0

        bundle = build_training_bundle(
            config,
            args.training_root,
            args.asset_manifest,
            validation_batch_size=args.validation_batch_size,
        )
        output_path = train_and_export(bundle)
        print(f"Training completed; runtime-verified ONNX model: {output_path}")
        return 0
    except TrainingBridgeError as exc:
        print(f"MINDER training bridge FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
