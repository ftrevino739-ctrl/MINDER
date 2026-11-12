"""Human-confirmation regression tests for irreversible and risky computer actions."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from actions import computer_settings as settings


class IrreversibleSettingsConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pyautogui = patch.object(settings, "_PYAUTOGUI", True)
        self._pyautogui.start()
        self.addCleanup(self._pyautogui.stop)

    def test_restart_shutdown_and_wifi_wait_for_the_human_gate(self) -> None:
        cases = (
            ("restart", "Restart this computer"),
            ("shutdown", "Shut this computer down"),
            ("toggle_wifi", "Switch WiFi off or on"),
        )
        for action, expected_title in cases:
            with self.subTest(action=action):
                captured = {}
                operation = unittest.mock.Mock()

                def request(**kwargs):
                    captured.update(kwargs)
                    return "[CONFIRMATION_PENDING]"

                with (
                    patch.object(settings, "ACTION_MAP",
                                 {**settings.ACTION_MAP, action: operation}),
                    patch.object(settings.confirm, "pending_title", return_value=""),
                    patch.object(settings.confirm, "request", side_effect=request) as gate,
                ):
                    result = settings.computer_settings({"action": action})
                    self.assertEqual(result, "[CONFIRMATION_PENDING]")
                    self.assertEqual(captured["title"], expected_title)
                    self.assertTrue(captured["detail"])
                    operation.assert_not_called()
                    gate.assert_called_once()
                    captured["run"]()
                    operation.assert_called_once_with()

    def test_existing_confirmation_blocks_a_second_irreversible_action(self) -> None:
        operation = unittest.mock.Mock()
        with (
            patch.object(settings, "ACTION_MAP",
                         {**settings.ACTION_MAP, "shutdown": operation}),
            patch.object(settings.confirm, "pending_title",
                         return_value="Restart this computer"),
            patch.object(settings.confirm, "request") as gate,
        ):
            result = settings.computer_settings({"action": "shutdown"})
        self.assertIn("already a confirmation waiting", result)
        gate.assert_not_called()
        operation.assert_not_called()

    def test_missing_confirmation_ui_fails_closed(self) -> None:
        operation = unittest.mock.Mock()
        with (
            patch.object(settings, "ACTION_MAP",
                         {**settings.ACTION_MAP, "restart": operation}),
            patch.object(settings.confirm, "pending_title", return_value=""),
            patch.object(settings.confirm, "_show_cb", None),
        ):
            result = settings.computer_settings({"action": "restart"})
        self.assertIn("cannot confirm", result.lower())
        self.assertIn("have not done it", result.lower())
        operation.assert_not_called()

    def test_model_supplied_confirmed_flag_cannot_bypass_human_gate(self) -> None:
        operation = unittest.mock.Mock()
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with (
            patch.object(settings, "ACTION_MAP",
                         {**settings.ACTION_MAP, "shutdown": operation}),
            patch.object(settings.confirm, "pending_title", return_value=""),
            patch.object(settings.confirm, "request", side_effect=request),
        ):
            result = settings.computer_settings({
                "action": "shutdown", "confirmed": True,
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            operation.assert_not_called()
            self.assertIn("Anything unsaved will be lost", captured["detail"])

    def test_wifi_confirmation_explains_connection_risk(self) -> None:
        captured = {}
        with (
            patch.object(settings.confirm, "pending_title", return_value=""),
            patch.object(settings.confirm, "request",
                         side_effect=lambda **kwargs:
                         captured.update(kwargs) or "[CONFIRMATION_PENDING]"),
        ):
            settings.computer_settings({"action": "toggle_wifi"})
        self.assertIn("MINDER loses its connection", captured["detail"])

    def test_enter_and_window_close_shortcuts_require_approval(self):
        cases = (
            ("enter", "Approve Enter in the active application"),
            ("close_window", "Approve closing the active window"),
            ("close_tab", "Approve closing the active tab"),
        )
        for action, title in cases:
            with self.subTest(action=action):
                captured = {}
                operation = unittest.mock.Mock()
                with (
                    patch.object(settings, "ACTION_MAP",
                                 {**settings.ACTION_MAP, action: operation}),
                    patch.object(settings.confirm, "request",
                                 side_effect=lambda **kwargs:
                                 captured.update(kwargs) or "[CONFIRMATION_PENDING]"),
                ):
                    result = settings.computer_settings({"action": action})
                    self.assertEqual(result, "[CONFIRMATION_PENDING]")
                    self.assertEqual(captured["title"], title)
                    operation.assert_not_called()
                    captured["run"]()
                    operation.assert_called_once_with()

    def test_press_key_enter_requires_approval(self):
        captured = {}
        with (
            patch.object(settings, "press_key") as press,
            patch.object(settings.confirm, "request",
                         side_effect=lambda **kwargs:
                         captured.update(kwargs) or "[CONFIRMATION_PENDING]"),
        ):
            result = settings.computer_settings({
                "action": "press_key", "value": "Enter",
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            press.assert_not_called()
            self.assertIn("Enter may submit", captured["detail"])
            captured["run"]()
            press.assert_called_once_with("Enter")

    def test_type_and_enter_requires_approval_of_exact_text(self):
        captured = {}
        with (
            patch.object(settings, "type_text") as type_text,
            patch.object(settings.confirm, "request",
                         side_effect=lambda **kwargs:
                         captured.update(kwargs) or "[CONFIRMATION_PENDING]"),
        ):
            result = settings.computer_settings({
                "action": "type_text",
                "value": "Send this to Alex",
                "press_enter": True,
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            type_text.assert_not_called()
            self.assertIn("Send this to Alex", captured["detail"])
            captured["run"]()
            type_text.assert_called_once_with("Send this to Alex", press_enter_after=True)


class XRandRTests(unittest.TestCase):
    def test_xrandr_brightness_uses_argument_vector_without_shell(self):
        class Completed:
            stdout = "eDP-1 connected primary 1920x1080+0+0\n    Brightness: 0.7\n"
            returncode = 0

        with patch.object(settings.subprocess, "run", return_value=Completed()) as run:
            result = settings._adjust_xrandr_brightness(0.1)

        self.assertTrue(result)
        run.assert_any_call(
            ["xrandr", "--output", "eDP-1", "--brightness", "0.800"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5,
        )
        self.assertTrue(all("shell" not in call.kwargs for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
