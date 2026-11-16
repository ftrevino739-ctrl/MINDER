import importlib.util
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "wake_word_training" / "validate_model.py"
SPEC = importlib.util.spec_from_file_location("minder_wake_validator", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _write_wav(path, *, rate=16000, channels=1, width=2, frames=2560):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * frames * channels * width)


class WakeWordValidationTests(unittest.TestCase):
    def test_counts_detection_after_two_consecutive_positive_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "positive.wav"
            _write_wav(path)
            numpy_stub = types.ModuleType("numpy")
            numpy_stub.frombuffer = lambda raw, dtype=None: [0] * (len(raw) // 2)

            class Model:
                def __init__(self):
                    self.calls = 0

                def predict(self, audio):
                    self.calls += 1
                    return {"hey_minder": 0.9}

            with patch.dict(sys.modules, {"numpy": numpy_stub}):
                detected, events, duration = VALIDATOR.score_file(
                    Model(), "hey_minder", path, threshold=0.5
                )

        self.assertTrue(detected)
        self.assertEqual(events, 1)
        self.assertAlmostEqual(duration, 0.16)

    def test_rejects_non_16khz_mono_pcm16_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wrong_format.wav"
            _write_wav(path, rate=44100, channels=2, width=2)
            numpy_stub = types.ModuleType("numpy")
            numpy_stub.frombuffer = lambda raw, dtype=None: [0] * (len(raw) // 2)
            with patch.dict(sys.modules, {"numpy": numpy_stub}):
                with self.assertRaisesRegex(ValueError, "expected 16 kHz mono PCM16"):
                    VALIDATOR.score_file(
                        object(), "hey_minder", path, threshold=0.5
                    )

    def test_rejects_model_output_without_expected_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "positive.wav"
            _write_wav(path)
            numpy_stub = types.ModuleType("numpy")
            numpy_stub.frombuffer = lambda raw, dtype=None: [0] * (len(raw) // 2)

            class WrongModel:
                def predict(self, audio):
                    return {"hey_jarvis": 0.99}

            with patch.dict(sys.modules, {"numpy": numpy_stub}):
                with self.assertRaisesRegex(RuntimeError, "no key containing"):
                    VALIDATOR.score_file(
                        WrongModel(), "hey_minder", path, threshold=0.5
                    )

    def test_wav_discovery_is_recursive_and_rejects_empty_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / "speaker_a"
            nested.mkdir()
            wav_path = nested / "sample.wav"
            _write_wav(wav_path)
            self.assertEqual(VALIDATOR.wav_files(root), [wav_path])
            empty = root / "speaker_b"
            empty.mkdir()
            with self.assertRaisesRegex(FileNotFoundError, "No .wav files"):
                VALIDATOR.wav_files(empty)


if __name__ == "__main__":
    unittest.main()
