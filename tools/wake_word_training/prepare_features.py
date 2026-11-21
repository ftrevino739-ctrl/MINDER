"""MINDER-owned, Windows-safe augmentation and feature-extraction stage.

This stage bridges manifest-approved multilingual Piper corpora and reviewed
room/background audio into the four openWakeWord .npy feature arrays used by
trainer_bridge.py. It is a build-time command, independent of MINDER's normal
voice/text assistant runtime. The default is read-only; actual feature
extraction requires two explicit confirmation flags.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tools.wake_word_training.trainer_bridge import (
    PINNED_OPENWAKEWORD_COMMIT,
    TrainingBridgeError,
    resolve_training_path,
    validate_minder_config,
    verify_pinned_openwakeword_checkout,
)
from tools.wake_word_training.piper_clip_generator import (
    PiperCorpusError,
    validate_piper_corpus_manifest,
)
from tools.wake_word_training.feature_writer import (
    FeatureWriterError,
    write_features_from_generator,
)


class FeaturePreparationError(ValueError):
    """Raised when corpus, augmentation source or output contracts are not met."""


_SPLITS = (
    ("positive_train", "positive_features_train", "n_samples"),
    ("positive_test", "positive_features_test", "n_samples_val"),
    ("negative_train", "negative_features_train", "n_samples"),
    ("negative_test", "negative_features_test", "n_samples_val"),
)


@dataclass
class FeaturePreparationBundle:
    """Read-only validated inputs for a feature extraction run."""

    training_root: Path
    asset_root: Path
    feature_dir: Path
    corpora: dict[str, list[Path]]
    corpus_asset_ids: dict[str, str]
    room_impulse_responses: list[Path]
    background_clips: list[Path]
    augmentation_rounds: int
    augmentation_batch_size: int
    clip_duration_samples: int
    output_paths: dict[str, Path]
    source_asset_ids: list[str]


def _read_mapping(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeaturePreparationError(f"Cannot read {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeaturePreparationError(f"{description} must contain a JSON object.")
    return payload


def _load_asset_index(manifest_path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_mapping(manifest_path, "training asset manifest")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise FeaturePreparationError("Training asset manifest must contain an assets list.")
    return {
        entry["asset_id"]: entry
        for entry in assets
        if isinstance(entry, dict) and isinstance(entry.get("asset_id"), str)
    }


def _relative_posix(path: Path, root: Path, description: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise FeaturePreparationError(f"{description} must be inside the reviewed asset root.") from exc


def _approved_flat_audio_files(
    directories: list[str],
    *,
    training_root: Path,
    asset_root: Path,
    asset_index: Mapping[str, Mapping[str, Any]],
    expected_asset_type: str,
    label: str,
    duplication_rates: list[int] | None = None,
) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    asset_ids: list[str] = []
    if duplication_rates is None:
        duplication_rates = [1] * len(directories)
    if len(duplication_rates) != len(directories):
        raise FeaturePreparationError(f"{label} duplication rates must match its directory count.")

    for directory_value, duplication in zip(directories, duplication_rates):
        if not isinstance(duplication, int) or isinstance(duplication, bool) or duplication < 1:
            raise FeaturePreparationError(f"{label} duplication rates must be positive integers.")
        folder = resolve_training_path(training_root, directory_value)
        if not folder.is_dir():
            raise FeaturePreparationError(f"{label} directory does not exist: {folder}")
        entries = sorted(folder.iterdir(), key=lambda path: path.name.casefold())
        if not entries:
            raise FeaturePreparationError(f"{label} directory is empty: {folder}")
        if any(not entry.is_file() for entry in entries):
            raise FeaturePreparationError(
                f"{label} directories must contain only top-level files: {folder}"
            )

        for path in entries:
            relative = _relative_posix(path, asset_root, f"{label} file")
            matching = [
                asset for asset in asset_index.values()
                if asset.get("relative_path") == relative
            ]
            if len(matching) != 1:
                raise FeaturePreparationError(
                    f"{label} file lacks one unique reviewed asset record: {relative}"
                )
            asset = matching[0]
            if asset.get("asset_type") != expected_asset_type:
                raise FeaturePreparationError(
                    f"{label} file {relative} must have asset_type {expected_asset_type!r}."
                )
            asset_id = asset["asset_id"]
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
            files.extend([path.resolve()] * duplication)

    return files, asset_ids


def _corpus_registration(
    *,
    split: str,
    corpus_manifest_path: Path,
    corpus_asset_id: str,
    expected_count: int,
    asset_root: Path,
    asset_manifest_path: Path,
    asset_index: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Path], list[str]]:
    manifest_path = corpus_manifest_path.expanduser().resolve()
    relative = _relative_posix(manifest_path, asset_root, f"{split} corpus manifest")
    asset = asset_index.get(corpus_asset_id)
    if not asset:
        raise FeaturePreparationError(
            f"{split} corpus asset id is absent from the parent asset manifest: {corpus_asset_id}"
        )
    if asset.get("asset_type") != "generated_audio_corpus_manifest":
        raise FeaturePreparationError(
            f"{split} corpus asset must have asset_type 'generated_audio_corpus_manifest'."
        )
    if asset.get("relative_path") != relative:
        raise FeaturePreparationError(
            f"{split} corpus asset points at a different path than {relative}."
        )
    if not manifest_path.is_file():
        raise FeaturePreparationError(f"{split} corpus manifest is missing: {manifest_path}")

    try:
        entries = validate_piper_corpus_manifest(
            manifest_path,
            manifest_path.parent,
            asset_manifest_path=asset_manifest_path,
            asset_root=asset_root,
            require_english_and_mexican_spanish=True,
        )
    except PiperCorpusError as exc:
        raise FeaturePreparationError(f"{split} Piper corpus rejected: {exc}") from exc

    if len(entries) != expected_count:
        raise FeaturePreparationError(
            f"{split} corpus has {len(entries)} WAV files; expected {expected_count}."
        )

    corpus_data = _read_mapping(manifest_path, f"{split} corpus manifest")
    source_ids = corpus_data.get("source_asset_ids", [])
    derived = asset.get("derived_from")
    if not isinstance(derived, list) or not set(source_ids).issubset(set(derived)):
        raise FeaturePreparationError(
            f"{split} corpus asset must list all voice model/config IDs under derived_from."
        )

    paths = [(manifest_path.parent / entry["relative_path"]).resolve() for entry in entries]
    return paths, [corpus_asset_id]


def _choose_clip_length(positive_test_paths: list[Path], seed: int) -> int:
    if not positive_test_paths:
        raise FeaturePreparationError("Positive validation corpus is empty.")
    rng = random.Random(seed)
    durations: list[int] = []
    for _ in range(min(50, len(positive_test_paths))):
        selected = positive_test_paths[rng.randrange(len(positive_test_paths))]
        try:
            with wave.open(str(selected), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                    raise FeaturePreparationError(
                        f"Positive validation WAV must be 16 kHz mono PCM16: {selected}"
                    )
                durations.append(wav.getnframes())
        except (OSError, wave.Error) as exc:
            raise FeaturePreparationError(f"Cannot inspect positive validation WAV {selected}: {exc}") from exc

    total = int(round(statistics.median(durations) / 1000) * 1000) + 12000
    if total < 32000 or abs(total - 32000) <= 4000:
        total = 32000
    return total


def validate_feature_preparation_workspace(
    config: Mapping[str, Any],
    training_root: Path,
    asset_root: Path,
    asset_manifest_path: Path,
    corpus_manifests: Mapping[str, Path],
    corpus_asset_ids: Mapping[str, str],
    *,
    seed: int = 20261121,
    require_pinned_checkout: bool = True,
) -> FeaturePreparationBundle:
    """Validate all corpus, RIR/background and output inputs without ML imports."""
    root = training_root.expanduser().resolve()
    assets_root = asset_root.expanduser().resolve()
    manifest_path = asset_manifest_path.expanduser().resolve()
    if not root.is_dir():
        raise FeaturePreparationError(f"Training root does not exist: {root}")
    if not assets_root.is_dir():
        raise FeaturePreparationError(f"Asset root does not exist: {assets_root}")
    if require_pinned_checkout:
        try:
            verify_pinned_openwakeword_checkout(root)
        except TrainingBridgeError as exc:
            raise FeaturePreparationError(str(exc)) from exc

    validate_minder_config(config)
    for split, _, _ in _SPLITS:
        if split not in corpus_manifests or split not in corpus_asset_ids:
            raise FeaturePreparationError(
                f"Missing corpus manifest or corpus asset ID for split {split!r}."
            )

    try:
        from tools.wake_word_training.asset_manifest import validate_manifest
        from tools.wake_word_training.asset_manifest import ManifestError
    except ImportError:
        from asset_manifest import validate_manifest, ManifestError

    try:
        validate_manifest(manifest_path, assets_root, verify_hashes=True, required_use="model_training")
    except ManifestError as exc:
        raise FeaturePreparationError(f"Training asset manifest rejected: {exc}") from exc
    asset_index = _load_asset_index(manifest_path)
    if not isinstance(config.get("rir_paths"), list) or not config["rir_paths"]:
        raise FeaturePreparationError("rir_paths must be a non-empty list.")
    if not isinstance(config.get("background_paths"), list) or not config["background_paths"]:
        raise FeaturePreparationError("background_paths must be a non-empty list.")
    rates = config.get("background_paths_duplication_rate")
    if not isinstance(rates, list) or len(rates) != len(config["background_paths"]):
        raise FeaturePreparationError("background_paths_duplication_rate must match background_paths.")
    if any(not isinstance(rate, int) or isinstance(rate, bool) or not 1 <= rate <= 20 for rate in rates):
        raise FeaturePreparationError("Background duplication rates must be integers between 1 and 20.")

    corpora: dict[str, list[Path]] = {}    source_asset_ids: list[str] = []
    for split, _, count_key in _SPLITS:
        expected_count = int(config[count_key])
        paths, corpus_ids = _corpus_registration(
            split=split,
            corpus_manifest_path=corpus_manifests[split],
            corpus_asset_id=corpus_asset_ids[split],
            expected_count=expected_count,
            asset_root=assets_root,
            asset_manifest_path=manifest_path,
            asset_index=asset_index,
        )
        corpora[split] = paths
        source_asset_ids.extend(corpus_ids)

    rir_paths, rir_asset_ids = _approved_flat_audio_files(
        list(config["rir_paths"]),
        training_root=root,
        asset_root=assets_root,
        asset_index=asset_index,
        expected_asset_type="room_impulse_response",
        label="Room impulse response",
    )
    rates = list(config.get("background_paths_duplication_rate") or [1] * len(config["background_paths"]))
    background_paths, background_asset_ids = _approved_flat_audio_files(
        list(config["background_paths"]),
        training_root=root,
        asset_root=assets_root,
        asset_index=asset_index,
        expected_asset_type="background_audio",
        label="Background audio",
        duplication_rates=rates,
    )

    output_dir = resolve_training_path(root, config["output_dir"])
    feature_dir = output_dir / config["model_name"]
    nearest_existing = output_dir
    while not nearest_existing.exists() and nearest_existing != nearest_existing.parent:
        nearest_existing = nearest_existing.parent
    if not nearest_existing.is_dir() or not os.access(nearest_existing, os.W_OK):
        raise FeaturePreparationError(
            f"No writable existing directory is available for feature output: {nearest_existing}"
        )
    _relative_posix(feature_dir, assets_root, "Feature output directory")
    proposal_path = feature_dir / "feature_asset_proposals.pending.json"
    if proposal_path.exists():
        raise FeaturePreparationError(
            f"Pending feature proposal already exists: {proposal_path}; review or archive it first."
        )
    output_paths = {
        file_key: feature_dir / f"{file_key}.npy"
        for _, file_key, _ in _SPLITS
    }
    existing_outputs = [str(path) for path in output_paths.values() if path.exists()]
    if existing_outputs:
        raise FeaturePreparationError(
            "Feature output already exists; use a fresh output directory rather than overwriting: "
            + ", ".join(existing_outputs)
        )

    clip_length_samples = _choose_clip_length(corpora["positive_test"], seed)
    source_asset_ids.extend(rir_asset_ids)
    source_asset_ids.extend(background_asset_ids)

    return FeaturePreparationBundle(
        training_root=root,
        asset_root=assets_root,
        feature_dir=feature_dir,
        corpora=corpora,
        corpus_asset_ids=dict(corpus_asset_ids),
        room_impulse_responses=rir_paths,
        background_clips=background_paths,
        augmentation_rounds=int(config["augmentation_rounds"]),
        augmentation_batch_size=int(config["augmentation_batch_size"]),
        clip_duration_samples=clip_length_samples,
        output_paths=output_paths,
        source_asset_ids=sorted(set(source_asset_ids)),
    )


def current_minder_source_revision() -> str:
    """Read the immutable local MINDER commit used as a transformation source."""
    repository_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeaturePreparationError(f"Cannot read MINDER source revision: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise FeaturePreparationError("Cannot determine the MINDER source commit for feature provenance.")
    return result.stdout.strip().lower()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _make_pending_feature_proposals(
    bundle: FeaturePreparationBundle,
    source_revision: str,
) -> Path:
    records: list[dict[str, Any]] = []
    source_url = (
        "https://github.com/ftrevino739-ctrl/MINDER/blob/main/"
        "tools/wake_word_training/prepare_features.py"
    )
    for split, filename, _ in _SPLITS:
        output_path = bundle.output_paths[filename]
        relative = _relative_posix(output_path, bundle.asset_root, f"{split} feature output")
        records.append({
            "asset_id": f"minder-hey-minder-{split}-features",
            "asset_type": "openwakeword_features",
            "source_url": source_url,
            "source_revision": source_revision,
            "relative_path": relative,
            "sha256": _hash_file(output_path),
            "license_id": "REVIEW_REQUIRED",
            "attribution": "Review all source corpus, room impulse response and background audio attributions before approval.",
            "restrictions": ["PENDING source-license and trained-feature review; do not distribute or train until approved."],
            "review_status": "pending",
            "reviewed_by": "",
            "reviewed_at": "",
            "approved_uses": [],
            "derived_from": list(bundle.source_asset_ids),
        })

    proposal_path = bundle.feature_dir / "feature_asset_proposals.pending.json"
    if proposal_path.exists():
        raise FeaturePreparationError(
            f"Refusing to replace existing feature proposal file: {proposal_path}"
        )
    temporary = proposal_path.with_name(f".{proposal_path.name}.{uuid.uuid4().hex}.partial")
    try:
        temporary.write_text(
            json.dumps({
                "proposal_schema_version": 1,
                "note": "PENDING human review. These are not approved asset records.",
                "assets": records,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.link(temporary, proposal_path)
    except OSError as exc:
        raise FeaturePreparationError(f"Could not publish pending feature proposals safely: {exc}") from exc
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass
    return proposal_path


def execute_feature_preparation(
    config: Mapping[str, Any],
    bundle: FeaturePreparationBundle,
    *,
    seed: int,
    dependencies: tuple[Any, Any, Any] | None = None,
) -> tuple[dict[str, Path], Path]:
    """Execute CPU-first augmentation and stream embeddings directly to disk."""
    if dependencies is None:
        try:
            import numpy as np
            import torch
            from openwakeword.data import augment_clips
            from openwakeword.utils import AudioFeatures
        except Exception as exc:
            raise FeaturePreparationError(
                f"Cannot import the pinned feature-preparation dependencies: {type(exc).__name__}: {exc}"
            ) from exc
    else:
        np, augment_clips, AudioFeatures = dependencies
        torch = None

    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    created_outputs: list[Path] = []
    try:
        bundle.feature_dir.mkdir(parents=True, exist_ok=True)
        audio_features = AudioFeatures(device="cpu")

        for split, filename, _ in _SPLITS:
            clip_paths = [str(path) for path in bundle.corpora[split]]
            clip_paths *= bundle.augmentation_rounds
            generator = augment_clips(
                clip_paths,
                total_length=bundle.clip_duration_samples,
                batch_size=bundle.augmentation_batch_size,
                background_clip_paths=[str(path) for path in bundle.background_clips],
                RIR_paths=[str(path) for path in bundle.room_impulse_responses],
            )
            write_features_from_generator(
                generator,
                total_samples=len(clip_paths),
                clip_duration_samples=bundle.clip_duration_samples,
                audio_features=audio_features,
                output_path=bundle.output_paths[filename],
            )
            created_outputs.append(bundle.output_paths[filename])

        revision = current_minder_source_revision()
        proposal_path = _make_pending_feature_proposals(bundle, revision)
        return bundle.output_paths, proposal_path
    except Exception as exc:
        for output_path in created_outputs:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, FeaturePreparationError):
            raise
        raise FeaturePreparationError(f"Feature preparation failed: {exc}") from exc

