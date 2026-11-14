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


def enqueue_microphone_frame(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    lock: threading.Lock,
    *,
    ptt_state: Callable[[], tuple[bool, bool]],
    data: bytes,
    mime_type: str = "audio/pcm",
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
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"data": bytes(data), "mime_type": mime_type},
        )
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
        try:
            queue.put_nowait(marker)
        except asyncio.QueueFull:
            # A full queue already means new microphone callbacks cannot safely
            # enqueue with put_nowait. Register the boundary as the first async
            # put waiter so it survives backpressure.
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
