"""Regression tests for bounded conversation history in the local pipeline."""
import unittest

from providers.engine_pipeline import PipelineEngine


class HistoryLLM:
    caps = {"tools": False, "vision": False, "tool_format": None}


class PipelineHistoryTests(unittest.TestCase):
    def test_oversized_latest_user_message_is_not_trimmed_away(self):
        engine = PipelineEngine(HistoryLLM(), history_chars=120)
        request = "older context " * 80 + "FINAL USER REQUEST"
        engine._history = [
            {"role": "user", "content": request},
        ]

        messages = engine._trimmed()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("FINAL USER REQUEST", messages[0]["content"])
        self.assertIn("truncated", messages[0]["content"].lower())
        self.assertLessEqual(len(messages[0]["content"]), 120)

    def test_recent_exchange_is_preserved_when_older_history_exceeds_budget(self):
        engine = PipelineEngine(HistoryLLM(), history_chars=100)
        engine._history = [
            {"role": "user", "content": "old context " * 30},
            {"role": "assistant", "content": "old answer " * 30},
            {"role": "user", "content": "What is the current project status?"},
        ]

        messages = engine._trimmed()

        self.assertEqual(messages, [engine._history[-1]])


if __name__ == "__main__":
    unittest.main()
