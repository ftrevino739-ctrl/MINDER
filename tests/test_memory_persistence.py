from __future__ import annotations

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from memory import memory_manager


class MemoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "long_term.json"
        self.path_patch = patch.object(memory_manager, "MEMORY_PATH", self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_save_memory_atomically_writes_complete_utf8_json(self):
        memory_manager.save_memory({
            "identity": {"language": {"value": "Español — México"}},
            "projects": {"MINDER": {"value": "Asistente de escritorio"}},
        })

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["identity"]["language"]["value"], "Español — México")
        self.assertEqual(saved["projects"]["MINDER"]["value"], "Asistente de escritorio")
        self.assertEqual(list(self.path.parent.glob(".long_term.json.*.tmp")), [])

    def test_replace_failure_preserves_previous_memory_and_cleans_temp_file(self):
        original = {"identity": {"display_name": {"value": "MINDER user"}}}
        memory_manager.save_memory(original)
        before = self.path.read_bytes()

        with patch.object(memory_manager.os, "replace",
                          side_effect=OSError("simulated disk/replace failure")):
            with self.assertRaises(OSError):
                memory_manager.save_memory({
                    "identity": {"display_name": {"value": "replacement"}}
                })

        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".long_term.json.*.tmp")), [])

    def test_concurrent_fact_and_session_writes_do_not_overwrite_each_other(self):
        original_update = memory_manager._recursive_update

        def slow_update(target, updates):
            # Widen the gap between the read and write so the test exercises
            # the whole transaction lock rather than depending on thread timing.
            time.sleep(0.002)
            return original_update(target, updates)

        tasks = []
        with patch.object(memory_manager, "_recursive_update", side_effect=slow_update):
            with ThreadPoolExecutor(max_workers=12) as pool:
                for index in range(24):
                    tasks.append(pool.submit(
                        memory_manager.update_memory,
                        {"projects": {f"project_{index}": f"status_{index}"}},
                    ))
                for index in range(12):
                    tasks.append(pool.submit(
                        memory_manager.save_session_summary,
                        f"Session {index} finished a MINDER development task.",
                        "es",
                    ))
                for task in tasks:
                    task.result(timeout=10)

        stored = memory_manager.load_memory()
        projects = stored.get("projects", {})
        self.assertEqual(len(projects), 24)
        for index in range(24):
            self.assertEqual(
                projects[f"project_{index}"]["value"],
                f"status_{index}",
            )

        sessions = stored.get("sessions", [])
        self.assertEqual(len(sessions), 3)
        self.assertTrue(all(item["summary"].startswith("Session ") for item in sessions))
        self.assertTrue(all(item["language"] == "es" for item in sessions))

    def test_forget_uses_the_same_atomic_persistence_path(self):
        memory_manager.remember("temporary_note", "remove me", "notes")

        result = memory_manager.forget("temporary_note", "notes")

        self.assertEqual(result, "Forgotten: notes/temporary_note")
        self.assertEqual(memory_manager.search_memory("temporary_note"), "Nothing stored about 'temporary_note'.")


if __name__ == "__main__":
    unittest.main()
