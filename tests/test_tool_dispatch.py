import asyncio
import unittest
from types import SimpleNamespace

from core.tool_dispatch import dispatch_function_calls


class ToolDispatchTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _calls(*items):
        return [SimpleNamespace(id=call_id, name=name) for call_id, name in items]

    @staticmethod
    def _response(**kwargs):
        return kwargs

    async def test_runs_every_call_in_order_without_interrupt(self):
        calls = self._calls(("1", "open_app"), ("2", "web_search"))
        executed = []

        async def execute(call):
            executed.append(call.id)
            return {"id": call.id, "result": "done"}

        responses = await dispatch_function_calls(
            calls,
            execute,
            is_interrupted=lambda: False,
            response_factory=self._response,
        )

        self.assertEqual(executed, ["1", "2"])
        self.assertEqual(responses, [
            {"id": "1", "result": "done"},
            {"id": "2", "result": "done"},
        ])

    async def test_interrupt_during_first_call_skips_remaining_side_effects(self):
        calls = self._calls(
            ("1", "save_memory"),
            ("2", "open_app"),
            ("3", "shutdown_jarvis"),
        )
        interrupted = asyncio.Event()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        executed = []

        async def execute(call):
            executed.append(call.id)
            if call.id == "1":
                first_started.set()
                await release_first.wait()
            return {"id": call.id, "result": "completed"}

        task = asyncio.create_task(dispatch_function_calls(
            calls,
            execute,
            is_interrupted=interrupted.is_set,
            response_factory=self._response,
        ))
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        interrupted.set()
        release_first.set()
        responses = await asyncio.wait_for(task, timeout=1.0)

        # The already-started operation is allowed to finish. The later
        # application launch and shutdown are never started.
        self.assertEqual(executed, ["1"])
        self.assertEqual(responses[0], {"id": "1", "result": "completed"})
        for response, expected_id in zip(responses[1:], ("2", "3")):
            self.assertEqual(response["id"], expected_id)
            self.assertEqual(response["name"], (
                "open_app" if expected_id == "2" else "shutdown_jarvis"
            ))
            self.assertIn("Not executed", response["response"]["error"])

    async def test_interrupt_before_batch_skips_every_call(self):
        calls = self._calls(("1", "open_app"), ("2", "delete_file"))
        executed = []

        async def execute(call):
            executed.append(call.id)
            return {"result": "unexpected"}

        responses = await dispatch_function_calls(
            calls,
            execute,
            is_interrupted=lambda: True,
            response_factory=self._response,
        )

        self.assertEqual(executed, [])
        self.assertEqual([item["id"] for item in responses], ["1", "2"])
        self.assertTrue(all("Not executed" in item["response"]["error"]
                            for item in responses))


if __name__ == "__main__":
    unittest.main()
