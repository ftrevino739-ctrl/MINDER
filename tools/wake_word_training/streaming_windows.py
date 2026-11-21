"""Reusable, bounded-memory batches for openWakeWord false-positive validation.

The upstream trainer creates every stride-1 feature window in one dense array.
For long validation streams, callers should use this iterator instead: it keeps
only one batch of copied windows resident at a time and can be iterated again
for each validation pass performed by Model.auto_train().
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def iter_window_slices(
    features: Any,
    window_size: int,
    batch_size: int,
) -> Iterator[tuple[list[Any], list[float]]]:
    """Yield ordered stride-1 feature-window batches and zero labels.

    features may be a NumPy array or read-only memmap with shape
    (frames, feature_dims). Windows intentionally match upstream's
    range(0, frames - window_size): the final mathematically possible
    window is excluded so validation counts stay comparable.

    The returned window elements are views/slices. The consumer should stack
    one yielded batch into a contiguous array before passing it to PyTorch.
    """
    shape = getattr(features, "shape", None)
    if shape is None or len(shape) != 2:
        raise ValueError("features must be a 2-D array shaped (frames, feature_dims)")
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0:
        raise ValueError("window_size must be a positive integer")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if shape[1] <= 0:
        raise ValueError("features must contain at least one feature dimension")

    window_count = max(0, int(shape[0]) - window_size)
    for batch_start in range(0, window_count, batch_size):
        batch_stop = min(window_count, batch_start + batch_size)
        windows = [
            features[index:index + window_size]
            for index in range(batch_start, batch_stop)
        ]
        yield windows, [0.0] * len(windows)


class TorchWindowBatches:
    """Re-iterable PyTorch batches without a giant overlapping tensor view.

    NumPy and PyTorch imports are intentionally lazy so MINDER's regular
    application and unit-test environments do not need training packages.
    Each call to iter() rebuilds batches from the same source array, which
    is required because openWakeWord's auto_train validates the stream several
    times and expects a fresh iterator on each pass.
    """

    def __init__(self, features: Any, window_size: int, batch_size: int = 8192):
        shape = getattr(features, "shape", None)
        if shape is None or len(shape) != 2:
            raise ValueError("features must be a 2-D array shaped (frames, feature_dims)")
        if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0:
            raise ValueError("window_size must be a positive integer")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if shape[1] <= 0:
            raise ValueError("features must contain at least one feature dimension")
        self.features = features
        self.window_size = window_size
        self.batch_size = batch_size
        self.window_count = max(0, int(shape[0]) - window_size)

    def __len__(self) -> int:
        """Number of batches per full validation pass."""
        return (self.window_count + self.batch_size - 1) // self.batch_size

    def __iter__(self):
        import numpy as np
        import torch

        for windows, labels in iter_window_slices(
            self.features, self.window_size, self.batch_size
        ):
            # Materialize only this bounded batch. This avoids wrapping a
            # non-writable, overlapping NumPy view with torch.from_numpy().
            batch_features = np.stack(windows).astype(np.float32, copy=False)
            batch_labels = np.asarray(labels, dtype=np.float32)
            yield torch.from_numpy(batch_features), torch.from_numpy(batch_labels)
