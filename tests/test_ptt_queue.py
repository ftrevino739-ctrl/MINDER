import asyncio
import threading
import unittest
from types import SimpleNamespace

from core.ptt_queue import (
    enqueue_microphone_frame,
    relay_outbound_audio,
    schedule_flush_barrier,
)


class FakeEngine:
    def __init__(self, events):
        self.events = events

    async def flush_microphone(self, sample_rate):
        self.events.append(("flush", sample_rate))


class FakeSession:
    def __init__(self, events, done):
        self.events = events
        self.done = done

    async def send_realtime_input(self, *, audio):
        self.events.append(("audio", audio.data))
        if len(self.events) == 3:
            self.done.set()


class PTTQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_ptt_enabled_rejects_frames_until_chord_is_held(self):
        queue = asyncio.Queue()
        accepted = enqueue_microphone_frame(
            asyncio.get_running_loop(),
            queue,
            threading.Lock(),
            ptt_state=lambda: (True, False),
            data=b"not-held",
        )
        await asyncio.sleep(0)

        self.assertFalse(accepted)
        self.assertTrue(queue.empty())

    async def test_release_barrier_stays_between_consecutive_ptt_turns(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=4)
        lock = threading.Lock()
        events = []
        done = asyncio.Event()
        engine = FakeEngine(events)
        session = FakeSession(events, done)

        enqueue_microphone_frame(
            loop, queue, lock,
            ptt_state=lambda: (True, True),
            data=b"first-turn",
        )
        with lock:
            schedule_flush_barrier(loop, queue, engine)
        enqueue_microphone_frame(
            loop, queue, lock,
            ptt_state=lambda: (True, True),
            data=b"second-turn",
        )

        relay = asyncio.create_task(
            relay_outbound_audio(queue, session, SimpleNamespace, 16000)
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=1.0)
        finally:
            relay.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await relay

        self.assertEqual(events, [
            ("audio", b"first-turn"),
            ("flush", 16000),
            ("audio", b"second-turn"),
        ])

    async def test_flush_barrier_is_not_lost_when_queue_is_full(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        lock = threading.Lock()
        events = []
        engine = FakeEngine(events)
        queue.put_nowait({"data": b"queued", "mime_type": "audio/pcm"})

        with lock:
            schedule_flush_barrier(loop, queue, engine)
        # Let the scheduled insertion observe the full queue and register its
        # async put waiter before freeing one slot.
        await asyncio.sleep(0)
        first = queue.get_nowait()
        self.assertEqual(first["data"], b"queued")

        marker = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertIs(marker.get("_flush_pipeline_turn"), engine)

    async def test_queue_overflow_reports_dropped_microphone_frames(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        lock = threading.Lock()
        dropped = []

        queue.put_nowait({"data": b"already-full"})
        accepted = enqueue_microphone_frame(
            loop, queue, lock,
            ptt_state=lambda: (False, False),
            data=b"new-frame",
            on_overflow=lambda: dropped.append(True),
        )
        await asyncio.sleep(0)

        self.assertTrue(accepted)  # accepted for scheduling, not delivery
        self.assertEqual(dropped, [True])
        self.assertEqual(queue.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
