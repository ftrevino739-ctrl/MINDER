"""Atomic, bounded-memory openWakeWord feature-file writer for Windows.

Unlike the upstream trim_mmap routine, this writer allocates the exact final
row count up front. It streams augmented PCM batches through AudioFeatures,
flushes/closes the mapping, and only then atomically replaces the destination.
No intermediate full-sized feature array is assembled in RAM.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterable


SAMPLE_RATE = 16000
FEATURE_DIMENSIONS = 96


class FeatureWriterError(ValueError):
    """Raised when a feature stream does not satisfy its declared output contract."""


def write_features_from_generator(
    generator: Iterable[Any],
    *,
    total_samples: int,
    clip_duration_samples: int,
    audio_features: Any,
    output_path: Path,
    overwrite: bool = False,
    numpy_module: Any | None = None,
) -> Path:
    """Write batches of augmented PCM clips directly to an exact-size .npy map.

    The input generator must yield arrays shaped (batch, PCM samples). Each
    call to audio_features.embed_clips() must return (batch, frames, 96)
    float32 features. The destination is published only after every expected
    row is written and the temporary memory map has been flushed and closed.

    Existing output is preserved on any failure. By default, an existing
    destination is rejected; set overwrite=True to replace it atomically
    after a complete new file has been written.
    """
    if isinstance(total_samples, bool) or not isinstance(total_samples, int) or total_samples <= 0:
        raise FeatureWriterError("total_samples must be a positive integer.")
    if (isinstance(clip_duration_samples, bool)
            or not isinstance(clip_duration_samples, int)
            or clip_duration_samples <= 0):
        raise FeatureWriterError("clip_duration_samples must be a positive integer.")

    destination = Path(output_path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise FeatureWriterError(f"Feature output directory does not exist: {destination.parent}")
    if destination.suffix.lower() != ".npy":
        raise FeatureWriterError("Feature output path must end with .npy.")
    if destination.exists() and not overwrite:
        raise FeatureWriterError(f"Refusing to replace existing feature file: {destination}")

    if numpy_module is None:
        try:
            import numpy as numpy_module
        except Exception as exc:
            raise FeatureWriterError(f"NumPy is unavailable: {exc}") from exc

    try:
        feature_shape = tuple(
            int(value)
            for value in audio_features.get_embedding_shape(clip_duration_samples / SAMPLE_RATE)
        )
    except Exception as exc:
        raise FeatureWriterError(f"Could not determine openWakeWord feature shape: {exc}") from exc

    if len(feature_shape) != 2 or feature_shape[0] <= 0 or feature_shape[1] != FEATURE_DIMENSIONS:
        raise FeatureWriterError(
            f"AudioFeatures must report (frames, {FEATURE_DIMENSIONS}); got {feature_shape}."
        )

    temporary = destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.partial.npy"
    )
    feature_map = None
    rows_written = 0

    try:
        feature_map = numpy_module.lib.format.open_memmap(
            str(temporary),
            mode="w+",
            dtype=numpy_module.float32,
            shape=(total_samples, *feature_shape),
        )

        for audio_batch in generator:
            pcm_shape = getattr(audio_batch, "shape", None)
            if pcm_shape is None or len(pcm_shape) != 2 or pcm_shape[0] <= 0:
                raise FeatureWriterError(
                    "Augmentation generator must yield non-empty 2-D PCM batches."
                )
            if pcm_shape[1] != clip_duration_samples:
                raise FeatureWriterError(
                    f"PCM batch length must be {clip_duration_samples} samples; got {pcm_shape[1]}."
                )
            if str(getattr(audio_batch, "dtype", "")) != "int16":
                raise FeatureWriterError("Augmentation batches must contain signed int16 PCM.")

            batch_count = int(pcm_shape[0])
            if rows_written + batch_count > total_samples:
                raise FeatureWriterError(
                    f"Augmentation generator produced more than the declared "
                    f"{total_samples} clips."
                )

            try:
                embedded = audio_features.embed_clips(
                    audio_batch,
                    batch_size=batch_count,
                )
                embedded = numpy_module.asarray(embedded, dtype=numpy_module.float32)
            except Exception as exc:
                raise FeatureWriterError(
                    f"Feature embedding failed at output row {rows_written}: {exc}"
                ) from exc

            expected_shape = (batch_count, *feature_shape)
            if tuple(getattr(embedded, "shape", ())) != expected_shape:
                raise FeatureWriterError(
                    f"Feature batch shape mismatch at row {rows_written}: "
                    f"expected {expected_shape}, got {getattr(embedded, 'shape', None)}."
                )

            feature_map[rows_written:rows_written + batch_count] = embedded
            rows_written += batch_count

        if rows_written != total_samples:
            raise FeatureWriterError(
                f"Augmentation generator produced {rows_written} clips; expected {total_samples}."
            )

        feature_map.flush()
        mapped_file = getattr(feature_map, "_mmap", None)
        if mapped_file is not None:
            mapped_file.close()
        feature_map = None

        # Same-directory replace is atomic on the supported local filesystems.
        os.replace(temporary, destination)
    except Exception:
        if feature_map is not None:
            try:
                feature_map.flush()
                mapped_file = getattr(feature_map, "_mmap", None)
                if mapped_file is not None:
                    mapped_file.close()
            except Exception:
                pass
            feature_map = None
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return destination
