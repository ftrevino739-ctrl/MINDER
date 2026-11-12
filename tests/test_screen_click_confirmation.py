"""Human approval tests for semantic screen clicks."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from actions import computer_control as computer_module


class ScreenClickConfirmationTests(unittest.TestCase):
    def test_sensitive_screen_click_waits_for_human_confirmation(self):
        captured = {}
        parameters = {"action": "screen_click", "description": "Send message"}

        with (
            patch.object(computer_module, "_PYAUTOGUI", True),
            patch.object(computer_module, "_screen_find") as find,
            patch.object(computer_module, "_click") as click,
            patch.object(
                computer_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = computer_module.computer_control(parameters)

        self.assertEqual(result, "PENDING")
        find.assert_not_called()
        click.assert_not_called()
        self.assertEqual(captured["title"], "Approve screen click: Send message")
        self.assertIn("Send message", captured["detail"])

    def test_sensitive_screen_click_runs_only_after_confirmation(self):
        captured = {}
        parameters = {"action": "screen_click", "description": "Delete account"}

        with (
            patch.object(computer_module, "_PYAUTOGUI", True),
            patch.object(computer_module, "_screen_find", return_value=(120, 240)) as find,
            patch.object(computer_module, "_click") as click,
            patch.object(
                computer_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = computer_module.computer_control(parameters)
            self.assertEqual(result, "PENDING")
            click.assert_not_called()
            confirmed = captured["run"]()

        self.assertIn("status=unverified", confirmed)
        find.assert_called_once_with("Delete account")
        click.assert_called_once_with(x=120, y=240)

    def test_missing_screen_target_does_not_claim_a_click(self):
        with (
            patch.object(computer_module, "_PYAUTOGUI", True),
            patch.object(computer_module, "_screen_find", return_value=None),
            patch.object(computer_module, "_click") as click,
            patch.object(
                computer_module.confirm, "request",
                side_effect=lambda **kwargs: kwargs["run"](),
            ),
        ):
            result = computer_module.computer_control({
                "action": "screen_click",
                "description": "Send message",
            })
            click.assert_not_called()

        self.assertIn("status=failed", result)
        self.assertIn("nothing was clicked", result)


if __name__ == "__main__":
    unittest.main()
