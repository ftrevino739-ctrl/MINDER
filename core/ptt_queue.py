"""Ordered microphone queue primitives for push-to-talk.

The PortAudio callback and hotkey callback run on different threads, while the
actual audio sender owns the asyncio queue. These helpers keep queue ordering
explicit without asking a callback thread to mutate asyncio.Queue directly.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import threading
from typing import Any, Callable


class MicrophoneQueue(asyncio.Queue):
    """Bounded audio queue with one reserved slot for PTT control barriers.

    Audio frames remain subject to the configured capacity. A turn-boundary
    marker may occupy one temporary extra slot when the queue is full, so a
    later microphone callback can never overtake the release boundary.
    """

    def put_barrier_nowait(self, marker: dict) -> None:
        if not self.full():
            self.put_nowait(marker)
            return

        # asyncio.Queue has no public "control item" API. Temporarily reserving
        # one slot lets the marker enter at the correct FIFO position; restoring
        # the limit immediately means subsequent PCM frames still backpressure
        # against the original bound until the queue drains.
        maxsize = self._maxsize
        self._maxsize = maxsize + 1
        try:
            self.put_nowait(marker)
        finally:
            self._maxsize = maxsize


def enqueue_microphone_frame(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    lock: threading.Lock,
    *,
    ptt_state: Callable[[], tuple[bool, bool]],
    data: bytes,
    mime_type: str = "audio/pcm",
    on_overflow: Callable[[], None] | None = None,
) -> bool:
    """Schedule one frame, rejecting it when PTT is enabled but not held.

    The caller and release callback must use the same lock. A frame accepted
    before release is therefore scheduled before the release marker; a frame
    arriving after release is rejected.
    """
    if loop is None or queue is None or loop.is_closed():
        return False
    with lock:
        # Read the state only after taking the lock. Passing pre-read booleans
        # allowed a callback to carry a stale "held" value across key release.
        ptt_enabled, ptt_held = ptt_state()
        if ptt_enabled and not ptt_held:
            return False
        payload = {"data": bytes(data), "mime_type": mime_type}

        def _put_frame():
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A slow/disconnected provider must not raise out of a Qt/IO
                # callback and poison the event loop. Report the dropped frame
                # to the owner; the bounded queue intentionally caps latency.
                if on_overflow is not None:
                    try:
                        on_overflow()
                    except Exception:
                        pass

        loop.call_soon_threadsafe(_put_frame)
    return True


def schedule_flush_barrier(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    engine: Any,
) -> None:
    """Schedule a flush marker after preceding frame callbacks.

    The caller must hold the same lock used by enqueue_microphone_frame while
    scheduling this marker. It is inserted synchronously on the event-loop
    thread, before later PTT presses can schedule another frame. If the bounded
    queue is full, a waiter preserves the marker rather than dropping the turn
    boundary.
    """
    if loop is None or queue is None or engine is None or loop.is_closed():
        return

    marker = {"_flush_pipeline_turn": engine}

    def _insert_marker():
        put_barrier = getattr(queue, "put_barrier_nowait", None)
        if callable(put_barrier):
            put_barrier(marker)
            return

        try:
            queue.put_nowait(marker)
        except asyncio.QueueFull:
            # Compatibility for callers still using a plain asyncio.Queue.
            # The application uses MicrophoneQueue so new frames cannot
            # overtake its reserved control marker under backpressure.
            asyncio.create_task(queue.put(marker))

    loop.call_soon_threadsafe(_insert_marker)


async def relay_outbound_audio(
    queue: asyncio.Queue,
    session: Any,
    blob_factory: Callable[..., Any],
    sample_rate: int,
) -> None:
    """Deliver queued PCM in FIFO order and flush STT at PTT barriers."""
    while True:
        msg = await queue.get()
        if isinstance(msg, dict) and "_flush_pipeline_turn" in msg:
            engine = msg.get("_flush_pipeline_turn")
            if engine is not None:
                await engine.flush_microphone(sample_rate)
            continue

        await session.send_realtime_input(
            audio=blob_factory(
                data=msg["data"],
                mime_type=msg.get("mime_type", "audio/pcm"),
            )
        )
