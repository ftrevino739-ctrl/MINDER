"""Regression tests for bounded wake-word false-positive validation windows."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "wake_word_training"
    / "streaming_windows.py"
)
SPEC = importlib.util.spec_from_file_location("minder_streaming_windows", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Matrix:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]) if rows else 0)

    def __getitem__(self, item):
        return self.rows[item]


class StreamingWindowTests(unittest.TestCase):
    def test_batches_match_upstream_stride_one_window_order_and_count(self):
        source = Matrix([[frame, frame + 100] for frame in range(11)])
        batches = list(MODULE.iter_window_slices(source, window_size=4, batch_size=3))

        actual_windows = [window for windows, _ in batches for window in windows]
        labels = [label for _, batch_labels in batches for label in batch_labels]
        expected = [source.rows[i:i + 4] for i in range(11 - 4)]

        self.assertEqual(actual_windows, expected)
        self.assertEqual(len(actual_windows), 7)
        self.assertEqual(labels, [0.0] * 7)
        self.assertEqual([len(windows) for windows, _ in batches], [3, 3, 1])

    def test_last_partial_batch_is_preserved(self):
        source = Matrix([[i] for i in range(8)])
        batches = list(MODULE.iter_window_slices(source, window_size=3, batch_size=4))
        self.assertEqual([len(windows) for windows, _ in batches], [4, 1])

    def test_short_stream_yields_no_windows(self):
        source = Matrix([[1], [2], [3]])
        self.assertEqual(
            list(MODULE.iter_window_slices(source, window_size=3, batch_size=2)),
            [],
        )

    def test_torch_batches_reports_bounded_length_without_importing_training_stack(self):
        source = Matrix([[i] for i in range(100)])
        batches = MODULE.TorchWindowBatches(source, window_size=16, batch_size=8)
        self.assertEqual(len(batches), 11)
        first_pass = list(MODULE.iter_window_slices(source, 16, 8))
        second_pass = list(MODULE.iter_window_slices(source, 16, 8))
        self.assertEqual(first_pass, second_pass)
        self.assertEqual(len(first_pass), len(batches))

    def test_torch_adapter_emits_writable_batch_values_and_restarts_each_pass(self):
        source = Matrix([[i, i + 10] for i in range(7)])
        batches = MODULE.TorchWindowBatches(source, window_size=3, batch_size=2)

        # The app CI deliberately does not install the optional ML training
        # stack. Lightweight stubs verify the adapter's batching contract.
        numpy_stub = types.ModuleType("numpy")
        numpy_stub.float32 = "float32"
        numpy_stub.stack = lambda values: [list(value) for value in values]
        numpy_stub.asarray = lambda values, dtype=None: list(values)
        torch_stub = types.ModuleType("torch")
        torch_stub.from_numpy = lambda value: value

        with patch.dict(sys.modules, {"numpy": numpy_stub, "torch": torch_stub}):
            first = list(batches)
            second = list(batches)

        self.assertEqual(first, second)
        self.assertEqual([len(x) for x, _ in first], [2, 2])
        self.assertEqual(
            first[0][0],
            [
                [[0, 10], [1, 11], [2, 12]],
                [[1, 11], [2, 12], [3, 13]],
            ],
        )
        self.assertEqual([labels for _, labels in first], [[0.0, 0.0], [0.0, 0.0]])

    def test_rejects_invalid_shape_and_sizes(self):
        with self.assertRaisesRegex(ValueError, "2-D array"):
            list(MODULE.iter_window_slices([1, 2, 3], 2, 1))
        with self.assertRaisesRegex(ValueError, "window_size"):
            list(MODULE.iter_window_slices(Matrix([[1], [2]]), 0, 1))
        with self.assertRaisesRegex(ValueError, "batch_size"):
            list(MODULE.iter_window_slices(Matrix([[1], [2]]), 1, 0))
        with self.assertRaisesRegex(ValueError, "feature dimension"):
            list(MODULE.iter_window_slices(Matrix([[], []]), 1, 1))


if __name__ == "__main__":
    unittest.main()
