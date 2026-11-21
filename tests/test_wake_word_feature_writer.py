"""Unit tests for atomic, bounded-memory openWakeWord feature writing."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from tools.wake_word_training import feature_writer as WRITER


class FakePcmBatch:
    def __init__(self, count, samples=32000):
        self.shape = (count, samples)
        self.dtype = "int16"


class FakeEmbeddings:
    def __init__(self, shape):
        self.shape = shape


class FakeMmap:
    def __init__(self, path, shape):
        self.path = Path(path)
        self.shape = tuple(shape)
        self.writes = []
        self.closed = False
        self.path.write_bytes(b"temporary feature map")

    def __setitem__(self, key, value):
        self.writes.append((key, value.shape))

    def flush(self):
        return None

    @property
    def _mmap(self):
        class Handle:
            def __init__(self, owner):
                self.owner = owner

            def close(self):
                self.owner.closed = True
        return Handle(self)


class FakeNumpy:
    float32 = "float32"

    def __init__(self):
        self.lib = type("Lib", (), {})()
        self.lib.format = type("Format", (), {})()
        self.opened = []

        def open_memmap(path, mode, dtype, shape):
            mapped = FakeMmap(path, shape)
            self.opened.append((path, mode, dtype, shape, mapped))
            return mapped

        self.lib.format.open_memmap = open_memmap

    @staticmethod
    def asarray(value, dtype=None):
        return value


class FakeAudioFeatures:
    def __init__(self, wrong_shape=False):
        self.calls = []
        self.wrong_shape = wrong_shape

    def get_embedding_shape(self, duration_seconds):
        self.duration_seconds = duration_seconds
        return (4, 96)

    def embed_clips(self, batch, batch_size):
        self.calls.append((batch.shape, batch_size))
        count = batch.shape[0]
        frames = 5 if self.wrong_shape else 4
        return FakeEmbeddings((count, frames, 96))


class FeatureWriterTests(unittest.TestCase):
    def test_writes_exact_rows_in_batches_and_closes_map_before_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "positive_features_train.npy"
            numpy = FakeNumpy()
            audio = FakeAudioFeatures()

            result = WRITER.write_features_from_generator(
                [FakePcmBatch(2), FakePcmBatch(1)],
                total_samples=3,
                clip_duration_samples=32000,
                audio_features=audio,
                output_path=output,
                numpy_module=numpy,
            )

            self.assertEqual(result, output)
            self.assertTrue(output.is_file())
            self.assertEqual(audio.duration_seconds, 2.0)
            self.assertEqual(audio.calls, [((2, 32000), 2), ((1, 32000), 1)])
            self.assertEqual(numpy.opened[0][3], (3, 4, 96))
            self.assertEqual(len(numpy.opened[0][4].writes), 2)
            self.assertTrue(numpy.opened[0][4].closed)
            self.assertFalse(list(root.glob("*.partial.npy")))
            self.assertFalse(list(root.glob(".*.partial.npy")))

    def test_rejects_existing_destination_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            output.write_bytes(b"previous good model data")
            before = output.read_bytes()
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "Refusing to replace"):
                WRITER.write_features_from_generator(
                    [], total_samples=1, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(), output_path=output,
                    numpy_module=FakeNumpy(),
                )
            self.assertEqual(output.read_bytes(), before)

    def test_overwrite_replaces_destination_only_after_complete_write(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            output.write_bytes(b"old valid data")
            WRITER.write_features_from_generator(
                [FakePcmBatch(1)], total_samples=1, clip_duration_samples=32000,
                audio_features=FakeAudioFeatures(), output_path=output,
                overwrite=True, numpy_module=FakeNumpy(),
            )
            self.assertNotEqual(output.read_bytes(), b"old valid data")

    def test_incomplete_generator_preserves_previous_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "features.npy"
            output.write_bytes(b"old file")
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "expected 3"):
                WRITER.write_features_from_generator(
                    [FakePcmBatch(2)], total_samples=3, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(), output_path=output,
                    overwrite=True, numpy_module=FakeNumpy(),
                )
            self.assertEqual(output.read_bytes(), b"old file")
            self.assertFalse(list(root.glob(".*.partial.npy")))

    def test_excess_generator_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "more than the declared"):
                WRITER.write_features_from_generator(
                    [FakePcmBatch(2), FakePcmBatch(2)],
                    total_samples=3,
                    clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(),
                    output_path=output,
                    numpy_module=FakeNumpy(),
                )
            self.assertFalse(output.exists())

    def test_rejects_non_int16_pcm_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            batch = FakePcmBatch(1)
            batch.dtype = "float32"
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "signed int16"):
                WRITER.write_features_from_generator(
                    [batch], total_samples=1, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(), output_path=output,
                    numpy_module=FakeNumpy(),
                )
            self.assertFalse(output.exists())

    def test_rejects_pcm_batch_with_wrong_clip_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "PCM batch length"):
                WRITER.write_features_from_generator(
                    [FakePcmBatch(1, samples=16000)],
                    total_samples=1,
                    clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(),
                    output_path=output,
                    numpy_module=FakeNumpy(),
                )
            self.assertFalse(output.exists())

    def test_rejects_feature_embedding_shape_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "features.npy"
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "Feature batch shape mismatch"):
                WRITER.write_features_from_generator(
                    [FakePcmBatch(1)],
                    total_samples=1,
                    clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(wrong_shape=True),
                    output_path=output,
                    numpy_module=FakeNumpy(),
                )
            self.assertFalse(output.exists())

    def test_rejects_missing_output_directory_and_wrong_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "directory does not exist"):
                WRITER.write_features_from_generator(
                    [], total_samples=1, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(),
                    output_path=root / "missing" / "x.npy",
                    numpy_module=FakeNumpy(),
                )
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "end with .npy"):
                WRITER.write_features_from_generator(
                    [], total_samples=1, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(),
                    output_path=root / "x.bin",
                    numpy_module=FakeNumpy(),
                )

    def test_rejects_invalid_total_and_clip_lengths_before_opening_file(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "x.npy"
            numpy = FakeNumpy()
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "total_samples"):
                WRITER.write_features_from_generator(
                    [], total_samples=0, clip_duration_samples=32000,
                    audio_features=FakeAudioFeatures(), output_path=output,
                    numpy_module=numpy,
                )
            with self.assertRaisesRegex(WRITER.FeatureWriterError, "clip_duration_samples"):
                WRITER.write_features_from_generator(
                    [], total_samples=1, clip_duration_samples=True,
                    audio_features=FakeAudioFeatures(), output_path=output,
                    numpy_module=numpy,
                )
            self.assertEqual(numpy.opened, [])


if __name__ == "__main__":
    unittest.main()
