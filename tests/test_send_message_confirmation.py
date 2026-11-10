"""Human-confirmation tests for externally sending messages."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from actions import send_message as send_message_module


class SendMessageConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = {
            "receiver": "Alex",
            "message_text": "I will be there at 6:30 PM.",
            "platform": "WhatsApp",
        }

    def test_message_is_not_sent_until_human_confirms(self) -> None:
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with (
            patch.object(send_message_module, "_PYAUTOGUI", True),
            patch.object(send_message_module.confirm, "pending_title", return_value=""),
            patch.object(send_message_module.confirm, "request", side_effect=request),
            patch.object(send_message_module, "_send_and_report") as send,
        ):
            result = send_message_module.send_message(self.parameters)

            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            send.assert_not_called()
            captured["run"]()
            send.assert_called_once_with(
                None, "WhatsApp", "Alex", "I will be there at 6:30 PM."
            )

    def test_confirmation_shows_exact_recipient_platform_and_message(self) -> None:
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "[CONFIRMATION_PENDING]"

        with (
            patch.object(send_message_module, "_PYAUTOGUI", True),
            patch.object(send_message_module.confirm, "pending_title", return_value=""),
            patch.object(send_message_module.confirm, "request", side_effect=request),
        ):
            send_message_module.send_message(self.parameters)

        self.assertEqual(captured["title"], "Send WhatsApp message to Alex")
        self.assertIn("Platform: WhatsApp", captured["detail"])
        self.assertIn("Recipient: Alex", captured["detail"])
        self.assertIn("I will be there at 6:30 PM.", captured["detail"])

    def test_existing_pending_confirmation_prevents_second_message(self) -> None:
        with (
            patch.object(send_message_module, "_PYAUTOGUI", True),
            patch.object(
                send_message_module.confirm, "pending_title",
                return_value="Restart this computer",
            ),
            patch.object(send_message_module.confirm, "request") as request,
            patch.object(send_message_module, "_send_and_report") as send,
        ):
            result = send_message_module.send_message(self.parameters)

        self.assertIn("already a confirmation waiting", result)
        request.assert_not_called()
        send.assert_not_called()

    def test_oversized_message_is_rejected_instead_of_truncated_for_approval(self) -> None:
        captured = {}
        parameters = {
            "receiver": "Alex",
            "platform": "WhatsApp",
            "message_text": "x" * (send_message_module.confirm.MAX_DETAIL_CHARS + 1),
        }

        with (
            patch.object(send_message_module, "_PYAUTOGUI", True),
            patch.object(send_message_module.confirm, "pending_title", return_value=""),
            patch.object(
                send_message_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "pending",
            ) as request,
            patch.object(send_message_module, "_send_and_report") as send,
        ):
            result = send_message_module.send_message(parameters)

        self.assertIn("too long to display in full", result)
        request.assert_not_called()
        send.assert_not_called()
        self.assertEqual(captured, {})

    def test_tool_description_explains_human_approval_and_delivery_states(self) -> None:
        description = send_message_module.TOOL["description"]
        self.assertIn("human approval on the HUD", description)
        self.assertIn("full message", description)
        self.assertIn("pending confirmation is NOT a sent message", description)
        self.assertIn("verified delivery", description)

    def test_missing_confirmation_ui_fails_closed(self) -> None:
        with (
            patch.object(send_message_module, "_PYAUTOGUI", True),
            patch.object(send_message_module.confirm, "pending_title", return_value=""),
            patch.object(send_message_module.confirm, "_show_cb", None),
            patch.object(send_message_module, "_send_and_report") as send,
        ):
            result = send_message_module.send_message(self.parameters)

        self.assertIn("cannot confirm", result.lower())
        self.assertIn("not done", result.lower())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
