"""
Local wake-word detection for JARVIS ("Hey Jarvis").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The pretrained wake phrase used here is "Hey Jarvis".
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis".
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    if not is_installed():
        return False
    try:
        import openwakeword
        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        if not models_dir.is_dir():
            return False
        has_wake = (any(models_dir.glob(f"{WAKE_MODEL}*.onnx"))
                    or any(models_dir.glob(f"{WAKE_MODEL}*.tflite")))
        has_mel = (any(models_dir.glob("melspectrogram*.onnx"))
                   or any(models_dir.glob("melspectrogram*.tflite")))
        has_emb = (any(models_dir.glob("embedding_model*.onnx"))
                   or any(models_dir.glob("embedding_model*.tflite")))
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            import openwakeword.utils as _u
            try:
                _u.download_models([WAKE_MODEL])
            except TypeError:
                _u.download_models()   # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs one isolated wake-model session in a dedicated thread. Each start gets
    its own queue, stop event and model reference. A quick stop/start therefore
    cannot revive an old worker or make it consume frames from the new session.
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger = logger
        self._notify = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._running = False
        self._model = None
        self._ready = False

    def start(self) -> bool:
        """Load the model and spawn an inference thread. Safe to call repeatedly."""
        if self._running and self._thread is not None and self._thread.is_alive():
            return True

        self._running = False
        self._ready = False
        try:
            from openwakeword.model import Model
            model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
            work_queue: queue.Queue = queue.Queue(maxsize=50)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(work_queue, stop_event, model),
                daemon=True,
                name="WakeWordThread",
            )
            self._queue = work_queue
            self._stop_event = stop_event
            self._model = model
            self._thread = thread
            self._running = True
            self._ready = True
            thread.start()
        except Exception as e:
            self._running = False
            self._ready = False
            self._model = None
            self._thread = None
            self._logger(f"Wake word: could not start detector — {e}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            return False

        self._logger("Wake word: listening for 'Hey Jarvis'.")
        return True

    def stop(self) -> None:
        """Stop the current session without allowing it to leak into a restart."""
        self._running = False
        stop_event = self._stop_event
        work_queue = self._queue
        if stop_event is not None:
            stop_event.set()

        # Drop buffered frames and wake a worker blocked in Queue.get().
        self._drain(work_queue)
        try:
            work_queue.put_nowait(None)
        except queue.Full:
            pass

        thread = self._thread
        self._thread = None
        self._model = None
        self._ready = False
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            # Do not freeze the Qt thread while an in-flight inference finishes.
            # The worker owns its old model/queue/event and cannot affect a restart.
            thread.join(timeout=0.25)

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Non-blocking mic callback: copy a frame and drop it if the queue is full."""
        work_queue = self._queue
        stop_event = self._stop_event
        if not self._running or stop_event is None or stop_event.is_set():
            return
        try:
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            work_queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self, work_queue: queue.Queue, stop_event: threading.Event, model) -> None:
        try:
            import numpy as np
            while not stop_event.is_set():
                try:
                    frame = work_queue.get()
                    if frame is None or stop_event.is_set():
                        break
                    scores = model.predict(np.asarray(frame, dtype=np.int16))
                    if stop_event.is_set():
                        break

                    score = 0.0
                    if isinstance(scores, dict):
                        for key, value in scores.items():
                            if "jarvis" in key.lower():
                                score = max(score, float(value))
                        if score == 0.0 and scores:
                            score = max(float(value) for value in scores.values())

                    if score >= self._threshold:
                        self._drain(work_queue)
                        if stop_event.is_set():
                            break
                        try:
                            self._on_detect()
                        except Exception as e:
                            self._logger(f"Wake word: on_detect error — {e}")
                except Exception as e:
                    if stop_event.is_set():
                        break
                    self._logger(f"Wake word: inference error — {e}")
        except Exception as e:
            if not stop_event.is_set():
                self._logger(f"Wake word: detector thread failed — {e}")
        finally:
            # An old worker must never clear the state of a newer session.
            if self._thread is threading.current_thread():
                self._running = False
                self._ready = False
                self._model = None

    @staticmethod
    def _drain(work_queue: queue.Queue) -> None:
        try:
            while True:
                work_queue.get_nowait()
        except queue.Empty:
            pass
