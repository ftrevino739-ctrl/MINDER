import sys
import threading
import types
import unittest
from unittest.mock import patch

from core.wake_word import WakeWordDetector


class _Frame:
    ndim = 1

    def copy(self):
        return self


def _mock_audio_modules(model_factory):
    package = types.ModuleType("openwakeword")
    package.__path__ = []
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = model_factory

    numpy_module = types.ModuleType("numpy")
    numpy_module.int16 = "int16"
    numpy_module.asarray = lambda frame, dtype=None: frame

    return {
        "openwakeword": package,
        "openwakeword.model": model_module,
        "numpy": numpy_module,
    }


class WakeWordLifecycleTests(unittest.TestCase):
    def test_stop_is_idempotent_and_releases_ready_state(self):
        class Model:
            def predict(self, frame):
                return {"hey_jarvis": 0.0}

        logs = []
        detector = WakeWordDetector(on_detect=lambda: None, logger=logs.append)
        with patch.dict(sys.modules, _mock_audio_modules(lambda **kwargs: Model())):
            self.assertTrue(detector.start())
            worker = detector._thread
            self.assertTrue(detector.ready)

            detector.stop()
            detector.stop()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertFalse(detector.ready)
        self.assertFalse(detector._running)

    def test_immediate_restart_does_not_revive_old_detector_session(self):
        old_predict_started = threading.Event()
        release_old_predict = threading.Event()
        new_predict_started = threading.Event()
        detections = []

        class OldModel:
            def predict(self, frame):
                old_predict_started.set()
                release_old_predict.wait(timeout=3.0)
                return {"hey_jarvis": 1.0}

        class NewModel:
            def predict(self, frame):
                new_predict_started.set()
                return {"hey_jarvis": 0.0}

        models = iter((OldModel(), NewModel()))
        detector = WakeWordDetector(on_detect=lambda: detections.append("detected"),
                                    logger=lambda _message: None)
        old_worker = None
        new_worker = None

        with patch.dict(sys.modules, _mock_audio_modules(lambda **kwargs: next(models))):
            try:
                self.assertTrue(detector.start())
                old_worker = detector._thread
                detector.feed(_Frame())
                self.assertTrue(old_predict_started.wait(timeout=1.0))

                # Stop while the old model is still inside predict(). Start a
                # second session before that old inference is allowed to finish.
                detector.stop()
                self.assertFalse(detector.ready)
                self.assertTrue(detector.start())
                new_worker = detector._thread
                detector.feed(_Frame())

                self.assertTrue(new_predict_started.wait(timeout=1.0))
                release_old_predict.set()
                old_worker.join(timeout=1.0)

                self.assertFalse(old_worker.is_alive())
                self.assertTrue(detector.ready)
                self.assertEqual(detections, [])
            finally:
                release_old_predict.set()
                detector.stop()
                if old_worker is not None:
                    old_worker.join(timeout=1.0)
                if new_worker is not None:
                    new_worker.join(timeout=1.0)

    def test_failed_model_start_stays_disabled(self):
        messages = []

        def fail_model(**kwargs):
            raise RuntimeError("missing ONNX runtime")

        detector = WakeWordDetector(
            on_detect=lambda: None,
            logger=messages.append,
            notify=messages.append,
        )
        with patch.dict(sys.modules, _mock_audio_modules(fail_model)):
            self.assertFalse(detector.start())

        self.assertFalse(detector.ready)
        self.assertFalse(detector._running)
        self.assertTrue(any("could not start detector" in item for item in messages))
        self.assertTrue(any("unavailable" in item for item in messages))


if __name__ == "__main__":
    unittest.main()
