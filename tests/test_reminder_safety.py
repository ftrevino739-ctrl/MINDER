from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from actions import reminder as reminder_module


class ReminderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patcher = patch.object(reminder_module, "_scripts_dir", return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_notify_script_uses_exclusive_creation(self):
        task_name = "MINDERReminder_20261112_120000_a1b2c3d4"
        path = reminder_module._write_notify_script(task_name, "A safe reminder", "windows")
        original = path.read_text(encoding="utf-8")
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            reminder_module._write_notify_script(task_name, "A different reminder", "windows")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_task_name_rejects_path_components(self):
        with self.assertRaisesRegex(ValueError, "Invalid reminder task name"):
            reminder_module._write_notify_script("../outside", "message", "windows")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_same_datetime_reminders_receive_distinct_task_names(self):
        tomorrow = datetime.now() + timedelta(days=1)
        date_text = tomorrow.strftime("%Y-%m-%d")
        time_text = tomorrow.strftime("%H:%M")
        task_names = []
        tokens = iter(("a1b2c3d4", "e5f6a7b8"))

        def register(target_dt, task_name, script_path, message):
            task_names.append(task_name)
            return task_name

        with (
            patch.object(reminder_module, "_get_os", return_value="windows"),
            patch.object(reminder_module.secrets, "token_hex", side_effect=lambda _n: next(tokens)),
            patch.object(reminder_module, "_schedule_windows", side_effect=register),
        ):
            first = reminder_module.reminder({
                "date": date_text, "time": time_text, "message": "first",
            })
            second = reminder_module.reminder({
                "date": date_text, "time": time_text, "message": "second",
            })

        self.assertIn("Reminder set", first)
        self.assertIn("Reminder set", second)
        self.assertEqual(len(set(task_names)), 2)
        self.assertTrue(all(name.startswith("MINDERReminder_") for name in task_names))
        self.assertEqual(len(list(self.root.glob("*.py"))), 2)


if __name__ == "__main__":
    unittest.main()
