"""Tests for human approval around opaque desktop input operations."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from actions import computer_control as computer


class ComputerControlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.installed = patch.object(computer, "_PYAUTOGUI", True)
        self.installed.start()
        self.addCleanup(self.installed.stop)

    def _capture_confirmation(self):
        captured = {}
        patcher = patch.object(
            computer.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def test_coordinate_click_waits_for_human_approval(self):
        captured = self._capture_confirmation()
        with patch.object(computer, "_click") as click:
            result = computer.computer_control({"action": "click", "x": 100, "y": 200})
            self.assertEqual(result, "PENDING")
            click.assert_not_called()
            self.assertIn("(100, 200)", captured["detail"])
            captured["run"]()
            click.assert_called_once_with(100, 200, "left", 1)

    def test_drag_waits_for_human_approval(self):
        captured = self._capture_confirmation()
        with patch.object(computer, "_drag") as drag:
            result = computer.computer_control({
                "action": "drag", "x1": 1, "y1": 2, "x2": 30, "y2": 40,
            })
            self.assertEqual(result, "PENDING")
            drag.assert_not_called()
            self.assertIn("(1, 2)", captured["detail"])
            captured["run"]()
            drag.assert_called_once_with(1, 2, 30, 40)

    def test_enter_and_delete_keys_wait_for_approval(self):
        for key in ("enter", "delete"):
            with self.subTest(key=key):
                captured = self._capture_confirmation()
                with patch.object(computer, "_press") as press:
                    result = computer.computer_control({"action": "press", "key": key})
                    self.assertEqual(result, "PENDING")
                    press.assert_not_called()
                    captured["run"]()
                    press.assert_called_once_with(key)

    def test_risky_shortcut_waits_for_approval(self):
        captured = self._capture_confirmation()
        with patch.object(computer, "_hotkey") as hotkey:
            result = computer.computer_control({
                "action": "hotkey", "keys": "ctrl+shift+delete",
            })
            self.assertEqual(result, "PENDING")
            hotkey.assert_not_called()
            self.assertIn("ctrl + shift + delete", captured["detail"])
            captured["run"]()
            hotkey.assert_called_once_with("ctrl", "shift", "delete")

    def test_smart_type_clear_first_waits_for_approval(self):
        captured = self._capture_confirmation()
        with patch.object(computer, "_smart_type") as smart_type:
            result = computer.computer_control({
                "action": "smart_type", "text": "replacement", "clear_first": True,
            })
            self.assertEqual(result, "PENDING")
            smart_type.assert_not_called()
            self.assertIn("replacement", captured["detail"])
            captured["run"]()
            smart_type.assert_called_once_with("replacement", clear_first=True)

    def test_ordinary_scroll_stays_immediate(self):
        with patch.object(computer, "_scroll", return_value="scrolled") as scroll:
            result = computer.computer_control({"action": "scroll", "direction": "down"})
        self.assertEqual(result, "scrolled")
        scroll.assert_called_once()

    def test_screen_find_uses_pipeline_without_a_gemini_key(self):
        from types import SimpleNamespace
        from memory import config_manager

        class FakeImage:
            def save(self, buffer, format):
                buffer.write(b"screen")

        ui = SimpleNamespace(
            size=lambda: (800, 600),
            screenshot=lambda: FakeImage(),
        )
        with (
            patch.object(config_manager, "get_provider_mode", return_value="pipeline"),
            patch.object(computer, "_get_api_key") as get_key,
            patch.object(computer, "_require_pyautogui"),
            patch.object(computer, "pyautogui", ui),
            patch("core.gemini.call", return_value=SimpleNamespace(text="25,30")) as call,
        ):
            result = computer._screen_find("Save")

        self.assertEqual(result, (25, 30))
        get_key.assert_not_called()
        call.assert_called_once()
        image_part = call.call_args.args[0][0]
        self.assertEqual(image_part["inline_data"]["mime_type"], "image/png")
        self.assertEqual(image_part["inline_data"]["data"], b"screen")

    def test_screen_find_in_gemini_mode_still_requires_a_gemini_key(self):
        from memory import config_manager

        with (
            patch.object(config_manager, "get_provider_mode", return_value="gemini"),
            patch.object(computer, "_get_api_key", return_value=""),
            patch("core.gemini.call") as call,
        ):
            result = computer._screen_find("Save")

        self.assertIsNone(result)
        call.assert_not_called()

    def test_unknown_semantic_screen_targets_are_reviewed(self):
        captured = self._capture_confirmation()
        with patch.object(computer, "_perform_screen_click") as click:
            result = computer.computer_control({
                "action": "screen_click", "description": "Continue to checkout",
            })
            self.assertEqual(result, "PENDING")
            click.assert_not_called()
            self.assertIn("Continue to checkout", captured["detail"])


if __name__ == "__main__":
    unittest.main()
