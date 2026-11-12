"""Concurrency and fail-closed tests for the human confirmation queue."""

from __future__ import annotations

import threading
import unittest

from core import confirm


class ConfirmationQueueTests(unittest.TestCase):
    def setUp(self):
        self.original_callbacks = (
            confirm._show_cb,
            confirm._hide_cb,
            confirm._log_cb,
        )
        with confirm._lock:
            self.original_pending = confirm._pending
            confirm._pending = None

        self.show_calls = []
        self.hide_calls = []
        confirm.bind(
            lambda title, detail: self.show_calls.append((title, detail)),
            lambda: self.hide_calls.append(True),
        )

    def tearDown(self):
        with confirm._lock:
            confirm._pending = self.original_pending
        confirm.bind(*self.original_callbacks)

    def test_second_request_cannot_replace_the_confirmation_on_screen(self):
        first_run = lambda: "first action"
        second_run = lambda: "second action"

        first = confirm.request("first", "First operation", "Review first", first_run)
        second = confirm.request("second", "Second operation", "Review second", second_run)

        self.assertIn("[CONFIRMATION_PENDING]", first)
        self.assertIn("[CONFIRMATION_BLOCKED]", second)
        self.assertIn("First operation", second)
        self.assertEqual(confirm.pending_title(), "First operation")
        self.assertEqual(self.show_calls, [("First operation", "Review first")])

    def test_simultaneous_requests_allow_exactly_one_pending_action(self):
        count = 12
        barrier = threading.Barrier(count)
        results = [None] * count

        def request(index):
            barrier.wait()
            results[index] = confirm.request(
                f"action-{index}",
                f"Action {index}",
                f"Details {index}",
                lambda: f"ran {index}",
            )

        workers = [
            threading.Thread(target=request, args=(index,))
            for index in range(count)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        pending = [value for value in results if "[CONFIRMATION_PENDING]" in value]
        blocked = [value for value in results if "[CONFIRMATION_BLOCKED]" in value]
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(blocked), count - 1)
        self.assertEqual(len(self.show_calls), 1)

    def test_failed_hud_callback_clears_its_pending_request(self):
        confirm.bind(
            lambda _title, _detail: (_ for _ in ()).throw(RuntimeError("HUD offline")),
            lambda: None,
        )

        result = confirm.request("broken", "Broken operation", "Details", lambda: "ran")

        self.assertIn("Nothing was done", result)
        self.assertEqual(confirm.pending_title(), "")

    def test_missing_hud_callback_refuses_without_queueing_action(self):
        confirm.bind(None, None)

        result = confirm.request("headless", "Headless operation", "Details", lambda: "ran")

        self.assertIn("cannot confirm", result.lower())
        self.assertEqual(confirm.pending_title(), "")


if __name__ == "__main__":
    unittest.main()
