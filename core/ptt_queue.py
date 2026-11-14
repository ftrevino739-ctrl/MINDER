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
    ptt_enabled: bool,
    ptt_held: bool,
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
) -> Future | None:
    """Enqueue a flush marker after preceding frame callbacks.

    The caller must hold the same lock used by enqueue_microphone_frame while
    scheduling this barrier. Its coroutine uses awaited queue.put(), so a full
    queue cannot silently drop the end-of-utterance marker.
    """
    if loop is None or queue is None or engine is None or loop.is_closed():
        return None

    async def _put():
        await queue.put({"_flush_pipeline_turn": engine})

    return asyncio.run_coroutine_threadsafe(_put(), loop)


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
