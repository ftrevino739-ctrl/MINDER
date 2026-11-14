from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

from core.ptt_queue import (
    enqueue_microphone_frame,
    relay_outbound_audio,
    schedule_flush_barrier,
)


class _RecordingPipeline:
    def __init__(self):
        self.received = []
        self.flushed = threading.Event()
        self.flush_count = None
        self.sample_rate = None

    async def send_realtime_input(self, *, audio):
        self.received.append((audio.data, audio.mime_type))

    async def flush_microphone(self, sample_rate):
        self.flush_count = len(self.received)
        self.sample_rate = sample_rate
        self.flushed.set()


class PushToTalkQueueOrderTests(unittest.TestCase):
    def test_release_flush_is_queued_after_every_accepted_microphone_frame(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_loop, loop, thread)

        queue = asyncio.Queue(maxsize=8)
        pipeline = _RecordingPipeline()
        sender = asyncio.run_coroutine_threadsafe(
            relay_outbound_audio(
                queue,
                pipeline,
                lambda data, mime_type: SimpleNamespace(
                    data=data, mime_type=mime_type),
                16000,
            ),
            loop,
        )
        self.addCleanup(self._cancel_sender, sender)

        lock = threading.Lock()
        frames = [bytes([value]) * 320 for value in (1, 2, 3, 4)]
        for frame in frames:
            accepted = enqueue_microphone_frame(
                loop, queue, lock,
                ptt_enabled=True, ptt_held=True,
                data=frame,
            )
            self.assertTrue(accepted)

        # The hotkey release updates the held state and schedules the marker
        # under the same lock as frame enqueue, exactly as main.py does.
        with lock:
            schedule_flush_barrier(loop, queue, pipeline)

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
        self.assertEqual(pipeline.sample_rate, 16000)

        self.assertFalse(enqueue_microphone_frame(
            loop, queue, lock,
            ptt_enabled=True, ptt_held=False,
            data=b"late frame",
        ))

    @staticmethod
    def _cancel_sender(sender):
        if sender.done():
            return
        sender.cancel()
        try:
            sender.result(timeout=2)
        except Exception:
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
