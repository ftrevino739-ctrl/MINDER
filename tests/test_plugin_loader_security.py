from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import plugin_loader


PLUGIN_TEMPLATE = """from pathlib import Path
Path({marker!r}).write_text("executed", encoding="utf-8")

PLUGIN = {{
    "name": {name!r},
    "description": "A harmless test plugin.",
    "parameters": {{
        "type": "OBJECT",
        "properties": {{}},
    }},
}}

def run(parameters):
    return "plugin ran"
"""


class PluginLoaderSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plugins_dir = Path(self.temp.name) / "plugins"
        self.plugins_dir.mkdir()
        self.marker = Path(self.temp.name) / "imported.txt"

    def tearDown(self):
        self.temp.cleanup()

    def _write_plugin(self, filename="sample.py", name="sample_plugin"):
        path = self.plugins_dir / filename
        path.write_text(
            PLUGIN_TEMPLATE.format(marker=str(self.marker), name=name),
            encoding="utf-8",
        )
        return path

    def test_discovery_does_not_execute_disabled_plugin_code(self):
        self._write_plugin()
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=False):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, set(), logger=lambda _message: None,
            )
            self.assertFalse(self.marker.exists())
            self.assertTrue(registry.has("sample_plugin"))
            self.assertFalse(registry.get_tool_declarations())
            rows = registry.list_for_ui()
            self.assertTrue(rows[0]["valid"])
            self.assertFalse(rows[0]["enabled"])
            self.assertIn("disabled", registry.run("sample_plugin", {}).lower())
            self.assertFalse(self.marker.exists())

    def test_plugin_import_occurs_only_after_explicit_enable(self):
        self._write_plugin()
        state = {"enabled": False}
        with patch.object(
            plugin_loader, "get_plugin_enabled",
            side_effect=lambda _name: state["enabled"],
        ):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, set(), logger=lambda _message: None,
            )
            self.assertFalse(self.marker.exists())
            self.assertFalse(registry.get_tool_declarations())

            state["enabled"] = True
            declarations = registry.get_tool_declarations()
            self.assertEqual(declarations[0]["name"], "sample_plugin")
            self.assertFalse(self.marker.exists())

            result = registry.run("sample_plugin", {})
            self.assertEqual(result, "plugin ran")
            self.assertTrue(self.marker.exists())

    def test_changed_file_is_not_imported_after_discovery(self):
        path = self._write_plugin()
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=False):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, set(), logger=lambda _message: None,
            )
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=True):
            result = registry.run("sample_plugin", {})
        self.assertIn("changed after discovery", result)
        self.assertFalse(self.marker.exists())

    def test_dynamic_metadata_is_rejected_without_importing_module(self):
        path = self.plugins_dir / "dynamic.py"
        path.write_text(
            "Path(" + repr(str(self.marker)) + ").write_text('executed')\n"
            "def build_metadata(): return {'name': 'dynamic', "
            "'description': 'dynamic metadata', "
            "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
            "PLUGIN = build_metadata()\n"
            "def run(parameters): return 'should not run'\n",
            encoding="utf-8",
        )
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=True):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, set(), logger=lambda _message: None,
            )
        self.assertFalse(self.marker.exists())
        self.assertFalse(registry.has("dynamic"))
        self.assertFalse(registry.list_for_ui()[0]["valid"])

    def test_colliding_core_tool_name_is_rejected_without_import(self):
        self._write_plugin(name="open_app")
        with patch.object(plugin_loader, "get_plugin_enabled", return_value=True):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, {"open_app"}, logger=lambda _message: None,
            )
        self.assertFalse(self.marker.exists())
        self.assertFalse(registry.has("open_app"))
        self.assertFalse(registry.list_for_ui()[0]["valid"])


if __name__ == "__main__":
    unittest.main()
