from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

from google.genai import types

from main import JarvisLive


class _RecordingPipeline:
    def __init__(self):
        self.received = []
        self.flushed = threading.Event()
        self.flush_count = None

    async def send_realtime_input(self, *, audio):
        self.received.append((audio.data, audio.mime_type))

    async def flush_microphone(self, sample_rate):
        self.flush_count = len(self.received)
        self.flushed.set()


class PushToTalkQueueOrderTests(unittest.TestCase):
    def test_release_flush_is_queued_after_every_accepted_microphone_frame(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: self._stop_loop(loop, thread))

        pipeline = _RecordingPipeline()
        app = object.__new__(JarvisLive)
        app._loop = loop
        app.out_queue = asyncio.Queue(maxsize=4)
        app.session = pipeline
        app._engine = pipeline
        app._ptt_enabled = True
        app._ptt_held = True
        app._ptt_queue_lock = threading.Lock()
        app._wake_enabled = False
        app._awake = True
        app.ui = SimpleNamespace(set_state=lambda _state: None)

        sender = asyncio.run_coroutine_threadsafe(
            JarvisLive._send_realtime(app), loop)
        self.addCleanup(lambda: self._cancel_sender(sender))

        frames = [bytes([value]) * 320 for value in (1, 2, 3)]
        for frame in frames:
            self.assertTrue(app._queue_mic_audio(frame))

        # Simulates the PTT keyboard callback releasing on its own thread.
        JarvisLive._on_ptt(app, False)

        self.assertTrue(pipeline.flushed.wait(timeout=3),
                        "release never flushed the pipeline")
        self.assertEqual(pipeline.received, [
            (frame, "audio/pcm") for frame in frames
        ])
        self.assertEqual(
            pipeline.flush_count,
            len(frames),
            "the STT flush overtook a microphone frame",
        )
        self.assertFalse(app._queue_mic_audio(b"late frame"))

    @staticmethod
    def _cancel_sender(sender):
        if sender.done():
            return
        sender.cancel()
        try:
            sender.result(timeout=2)
        except (asyncio.CancelledError, Exception):
            pass

    @staticmethod
    def _stop_loop(loop, thread):
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        if not loop.is_closed():
            loop.close()


if __name__ == "__main__":
    unittest.main()
