#!/usr/bin/env python3
"""Read-only preflight for the Hey MINDER openWakeWord training workspace.

This script never downloads datasets, creates directories, records audio, or
starts training. It only checks the supplied checkout/configuration and disk.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path

REQUIRED_CONFIG = {
    "model_name", "target_phrase", "custom_negative_phrases", "n_samples",
    "n_samples_val", "tts_batch_size", "augmentation_batch_size",
    "piper_sample_generator_path", "output_dir", "rir_paths",
    "background_paths", "background_paths_duplication_rate",
    "false_positive_validation_data_path", "augmentation_rounds",
    "feature_data_files", "batch_n_per_class", "model_type", "layer_size",
    "steps", "max_negative_weight", "target_false_positives_per_hour",
}


def load_config(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is missing. Install it in the training environment.") from exc
    with path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("Training config must contain a YAML mapping.")
    return config


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def inspect_workspace(root: Path, config_path: Path, min_free_gb: float) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        return [f"Training root does not exist: {root}"], warnings
    if not config_path.is_file():
        return [f"Training config does not exist: {config_path}"], warnings

    try:
        config = load_config(config_path)
    except Exception as exc:
        return [f"Cannot load training config: {exc}"], warnings

    missing_keys = sorted(REQUIRED_CONFIG - config.keys())
    if missing_keys:
        errors.append("Missing config keys: " + ", ".join(missing_keys))

    if config.get("model_name") != "hey_minder":
        errors.append("model_name must be exactly 'hey_minder'.")
    phrases = config.get("target_phrase")
    if not isinstance(phrases, list) or "hey minder" not in [str(x).strip().lower() for x in phrases]:
        errors.append("target_phrase must be a list containing 'hey minder'.")
    if not isinstance(config.get("custom_negative_phrases"), list):
        errors.append("custom_negative_phrases must be a list.")
    if config.get("model_type") not in ("dnn", "rnn"):
        errors.append("model_type must be 'dnn' or 'rnn'.")
    try:
        fp_target = float(config.get("target_false_positives_per_hour"))
        if fp_target <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("target_false_positives_per_hour must be a positive number.")
    try:
        sample_count = int(config.get("n_samples", 0))
    except (TypeError, ValueError):
        sample_count = 0
        errors.append("n_samples must be a positive integer.")
    if sample_count < 20000:
        warnings.append("Fewer than 20,000 positive training samples are configured.")
    feature_files = config.get("feature_data_files")
    if not isinstance(feature_files, dict) or not feature_files:
        errors.append("feature_data_files must be a non-empty mapping of class names to .npy paths.")
        feature_files = {}
    elif "ACAV100M_sample" not in feature_files:
        errors.append("feature_data_files must include the ACAV100M_sample negative dataset.")

    batch_sizes = config.get("batch_n_per_class")
    if not isinstance(batch_sizes, dict):
        errors.append("batch_n_per_class must be a mapping.")
    else:
        expected_classes = set(feature_files) | {"positive", "adversarial_negative"}
        missing_batches = sorted(expected_classes - batch_sizes.keys())
        if missing_batches:
            errors.append("batch_n_per_class is missing: " + ", ".join(missing_batches))
        for label, value in batch_sizes.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"batch_n_per_class[{label!r}] must be a positive integer.")

    for key in ("n_samples_val", "tts_batch_size", "augmentation_batch_size",
                "augmentation_rounds", "steps", "layer_size", "max_negative_weight"):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"{key} must be a positive integer.")

    rates = config.get("background_paths_duplication_rate", [])
    background_paths = config.get("background_paths", [])
    if not isinstance(rates, list) or not isinstance(background_paths, list):
        errors.append("background_paths and background_paths_duplication_rate must be lists.")
    elif rates and len(rates) != len(background_paths):
        errors.append("background_paths_duplication_rate must match background_paths length.")
    elif any(not isinstance(rate, int) or isinstance(rate, bool) or rate < 1 for rate in rates):
        errors.append("Every background duplication rate must be a positive integer.")

    trainer = root / "openwakeword" / "train.py"
    if not trainer.is_file():
        errors.append(f"openWakeWord trainer not found: {trainer}")

    piper = resolve(root, str(config.get("piper_sample_generator_path", "")))
    if not piper.is_dir() or not (piper / "generate_samples.py").is_file():
        errors.append(f"Piper sample generator is missing or incomplete: {piper}")

    for key in ("rir_paths", "background_paths"):
        paths = config.get(key, [])
        if not isinstance(paths, list) or not paths:
            errors.append(f"{key} must be a non-empty list of directories.")
            continue
        for item in paths:
            folder = resolve(root, str(item))
            if not folder.is_dir():
                errors.append(f"{key} directory is missing: {folder}")
            # Upstream train.py uses os.scandir(folder), not rglob(); nested
            # files are invisible to it and would otherwise fail at runtime.
            elif not any(p.is_file() for p in folder.iterdir()):
                errors.append(f"{key} directory contains no top-level files: {folder}")

    validation = resolve(root, str(config.get("false_positive_validation_data_path", "")))
    if not validation.is_file():
        errors.append(f"False-positive validation feature file is missing: {validation}")

    if isinstance(feature_files, dict):
        for name, value in feature_files.items():
            feature_file = resolve(root, str(value))
            if not feature_file.is_file():
                errors.append(f"Feature dataset '{name}' is missing: {feature_file}")

    for module in ("numpy", "scipy", "torch", "yaml"):
        if importlib.util.find_spec(module) is None:
            errors.append(f"Python dependency is missing: {module}")

    output_dir = resolve(root, str(config.get("output_dir", "")))
    output_parent = output_dir if output_dir.exists() else output_dir.parent
    if not output_parent.is_dir():
        errors.append(f"Output directory parent does not exist: {output_parent}")
    elif not os.access(output_parent, os.W_OK):
        errors.append(f"Output directory parent is not writable: {output_parent}")

    usage = shutil.disk_usage(root)
    free_gb = usage.free / (1024 ** 3)
    print(f"Training root: {root}")
    print(f"Config: {config_path}")
    print(f"Free disk space: {free_gb:.1f} GiB")
    if free_gb < min_free_gb:
        warnings.append(
            f"Only {free_gb:.1f} GiB free; configured safety threshold is {min_free_gb:.1f} GiB. "
            "This is a warning, not a storage estimate."
        )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-root", type=Path, required=True,
                        help="Root of the official openWakeWord training checkout")
    parser.add_argument("--config", type=Path, required=True,
                        help="Path to hey_minder.yml (absolute or relative to training root)")
    parser.add_argument("--min-free-gb", type=float, default=20.0,
                        help="Warn below this free-space threshold (default: 20 GiB)")
    args = parser.parse_args()
    if args.min_free_gb < 0:
        parser.error("--min-free-gb cannot be negative")

    root = args.training_root.expanduser().resolve()
    config = args.config.expanduser()
    if not config.is_absolute():
        config = (root / config).resolve()
    else:
        config = config.resolve()

    errors, warnings = inspect_workspace(root, config, args.min_free_gb)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if errors:
        print(f"Preflight FAILED: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    print(f"Preflight PASSED: no missing prerequisites; {len(warnings)} warning(s).")
    print("No files were changed and no training was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
