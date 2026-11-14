import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from core import hotkey


class PushToTalkTests(unittest.TestCase):
    def test_window_scope_reports_press_and_release(self):
        events = []
        ptt = hotkey.PushToTalk(events.append, chord=("ctrl", "space"))
        with patch.object(hotkey, "_OS", "Linux"):
            self.assertEqual(ptt.start(), "window")
            ptt.set_held(True)
            ptt.set_held(True)
            ptt.set_held(False)
            ptt.stop()

        self.assertEqual(events, [True, False])
        self.assertFalse(ptt.held)
        self.assertEqual(ptt.scope, "window")

    def test_listener_exception_does_not_break_state(self):
        def broken_listener(_held):
            raise RuntimeError("UI callback failed")

        ptt = hotkey.PushToTalk(broken_listener)
        with patch.object(hotkey, "_OS", "Linux"):
            ptt.start()
            ptt.set_held(True)
            self.assertTrue(ptt.held)
            ptt.set_held(False)
            self.assertFalse(ptt.held)
            ptt.stop()

    def test_old_poll_worker_cannot_release_a_new_session(self):
        first_call_entered = threading.Event()
        release_first_call = threading.Event()
        callback_events = []

        class User32:
            def __init__(self):
                self._lock = threading.Lock()
                self._calls = 0

            def GetAsyncKeyState(self, _key):
                with self._lock:
                    self._calls += 1
                    call_number = self._calls
                if call_number == 1:
                    first_call_entered.set()
                    release_first_call.wait(timeout=4.0)
                    return 0
                return 0x8000

        user32 = User32()
        fake_ctypes = types.ModuleType("ctypes")
        fake_ctypes.windll = types.SimpleNamespace(user32=user32)

        ptt = hotkey.PushToTalk(callback_events.append, chord=("ctrl", "space"))
        stopper = None
        old_worker = None
        try:
            with (
                patch.object(hotkey, "_OS", "Windows"),
                patch.object(ptt, "_can_poll", return_value=True),
                patch.dict(sys.modules, {"ctypes": fake_ctypes}),
            ):
                self.assertEqual(ptt.start(), "global")
                old_worker = ptt._thread
                self.assertTrue(first_call_entered.wait(timeout=1.0))

                # stop() detaches the old worker before its bounded join. Start
                # another session while that old Win32 call is still blocked.
                stopper = threading.Thread(target=ptt.stop, daemon=True)
                stopper.start()
                old_stop_event = ptt._stop
                deadline = time.monotonic() + 1.0
                while not old_stop_event.is_set() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(old_stop_event.is_set())

                self.assertEqual(ptt.start(), "global")
                new_worker = ptt._thread
                deadline = time.monotonic() + 1.0
                while not callback_events and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(callback_events, [True])
                self.assertTrue(ptt.held)
                self.assertEqual(ptt.scope, "global")

                # Let the old poll finish after the new session has gone active.
                release_first_call.set()
                old_worker.join(timeout=1.0)
                self.assertFalse(old_worker.is_alive())
                self.assertEqual(callback_events, [True])
                self.assertTrue(ptt.held)
                self.assertEqual(ptt.scope, "global")

                # The active session still owns release notification.
                ptt.stop()
                self.assertEqual(callback_events, [True, False])
                new_worker.join(timeout=1.0)
        finally:
            release_first_call.set()
            ptt.stop()
            if stopper is not None:
                stopper.join(timeout=1.5)
            if old_worker is not None:
                old_worker.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
