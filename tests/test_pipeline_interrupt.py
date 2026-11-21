"""Thread-safe local-pipeline interruption regression tests."""
import asyncio
import unittest

from providers.base import Msg
from providers.engine_pipeline import PipelineEngine


class BlockingLLM:
    caps = {"tools": False, "vision": False, "tool_format": None}

    async def chat(self, messages, tools=None, *, stream=True):
        # Provide enough trailing content for the streaming reasoning filter
        # to release the first complete sentence to TTS.
        yield {"text": "I am speaking right now. Here is more streamed text."}
        await asyncio.Event().wait()


class BlockingTTS:
    def __init__(self):
        self.cancelled = asyncio.Event()

    async def speak(self, text, voice="", **kwargs):
        try:
            yield b"\\x00\\x00" * 128
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


class PipelineInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def _start_speaking_engine(self):
        tts = BlockingTTS()
        engine = PipelineEngine(BlockingLLM(), tts=tts)
        received = []
        first_audio = asyncio.Event()
        turn_ended = asyncio.Event()

        async def pump_messages():
            async for message in engine.receive():
                received.append(message)
                if message.data:
                    first_audio.set()
                if (message.server_content is not None
                        and message.server_content.turn_complete):
                    turn_ended.set()

        receiver = asyncio.create_task(pump_messages())
        await engine.send_client_content(
            turns={"role": "user", "parts": [{"text": "Please speak."}]}
        )
        await asyncio.wait_for(first_audio.wait(), timeout=2.0)
        return engine, tts, received, turn_ended, receiver

    async def test_interrupt_from_ui_thread_cancels_tts_and_closes_main_turn(self):
        engine, tts, received, turn_ended, receiver = await self._start_speaking_engine()
        owning_loop = asyncio.get_running_loop()
        self.assertIs(engine._loop, owning_loop)

        # Qt/hotkey callbacks run outside the asyncio loop in main.py.
        await asyncio.wait_for(asyncio.to_thread(engine.interrupt), timeout=2.0)
        await asyncio.wait_for(turn_ended.wait(), timeout=2.0)
        await asyncio.wait_for(tts.cancelled.wait(), timeout=2.0)
        await asyncio.sleep(0)

        terminal_events = [
            message for message in received
            if message.server_content is not None and message.server_content.turn_complete
        ]
        self.assertEqual(len(terminal_events), 1)
        self.assertTrue(any(message.data for message in received))
        self.assertTrue(engine._gen.done())
        self.assertFalse(engine._followup)

        await engine.close()
        await asyncio.wait_for(receiver, timeout=2.0)

    async def test_interrupt_on_own_loop_uses_same_completion_path(self):
        engine, tts, received, turn_ended, receiver = await self._start_speaking_engine()
        engine.interrupt()
        await asyncio.wait_for(turn_ended.wait(), timeout=2.0)
        await asyncio.wait_for(tts.cancelled.wait(), timeout=2.0)

        terminal_events = [
            message for message in received
            if message.server_content is not None and message.server_content.turn_complete
        ]
        self.assertEqual(len(terminal_events), 1)
        self.assertTrue(engine._gen.done())

        await engine.close()
        await asyncio.wait_for(receiver, timeout=2.0)

    async def test_interrupt_without_an_open_loop_is_a_safe_noop(self):
        engine = PipelineEngine(BlockingLLM())
        engine.interrupt()
        self.assertIsNone(engine._loop)
        self.assertFalse(engine._closed)
        await engine.close()


if __name__ == "__main__":
    unittest.main()