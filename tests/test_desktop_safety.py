"""Pure tests for dangerous key and shortcut classification."""
from __future__ import annotations
import unittest
from actions._desktop_safety import (
    is_risky_desktop_hotkey, is_risky_desktop_key, requires_desktop_confirmation,
)


class DesktopSafetyTests(unittest.TestCase):
    def test_submitting_and_deleting_keys_are_risky(self):
        for key in ("enter", "Return", "numpadenter", "delete", "Del"):
            with self.subTest(key=key):
                self.assertTrue(is_risky_desktop_key(key))
        for key in ("escape", "tab", "f5", "a", "backspace"):
            with self.subTest(key=key):
                self.assertFalse(is_risky_desktop_key(key))

    def test_destructive_or_submission_shortcuts_are_risky(self):
        for shortcut in (
            "alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "command+w",
            "command+q", "ctrl+shift+delete", "shift+delete",
            "ctrl+enter", "command+enter", "ctrl+alt+delete", "win+r",
        ):
            with self.subTest(shortcut=shortcut):
                self.assertTrue(is_risky_desktop_hotkey(shortcut))

    def test_shortcut_order_and_platform_aliases_are_normalized(self):
        self.assertTrue(is_risky_desktop_hotkey(["shift", "ctrl", "delete"]))
        self.assertTrue(is_risky_desktop_hotkey(["cmd", "enter"]))
        self.assertTrue(is_risky_desktop_hotkey(["windows", "r"]))

    def test_ordinary_shortcuts_are_not_gated(self):
        for shortcut in ("ctrl+c", "ctrl+v", "ctrl+a", "ctrl+t", "ctrl+tab", "f11"):
            with self.subTest(shortcut=shortcut):
                self.assertFalse(is_risky_desktop_hotkey(shortcut))

    def test_confirmation_policy_covers_opaque_desktop_actions(self):
        self.assertTrue(requires_desktop_confirmation("click"))
        self.assertTrue(requires_desktop_confirmation("drag"))
        self.assertTrue(requires_desktop_confirmation("clear_field"))
        self.assertTrue(requires_desktop_confirmation("press", key="Enter"))
        self.assertTrue(requires_desktop_confirmation("hotkey", keys="alt+f4"))
        self.assertTrue(requires_desktop_confirmation("smart_type", clear_first=True))
        self.assertTrue(requires_desktop_confirmation("type_text", press_enter=True))
        self.assertFalse(requires_desktop_confirmation("press", key="escape"))
        self.assertFalse(requires_desktop_confirmation("smart_type", clear_first=False))


if __name__ == "__main__":
    unittest.main()
