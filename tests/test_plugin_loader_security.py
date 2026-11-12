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
            side_effect=lambda _name, _digest: state["enabled"],
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

    def test_source_hash_must_match_persisted_approval_after_restart(self):
        import hashlib
        path = self._write_plugin()
        approved_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")

        with patch.object(
            plugin_loader, "get_plugin_enabled",
            side_effect=lambda _name, digest: digest == approved_hash,
        ):
            registry = plugin_loader.discover_plugins(
                self.plugins_dir, set(), logger=lambda _message: None,
            )
            self.assertFalse(registry.get_tool_declarations())
            result = registry.run("sample_plugin", {})
        self.assertIn("disabled", result.lower())
        self.assertFalse(self.marker.exists())

    def test_plugin_enablement_persists_the_approved_source_hash(self):
        import json
        from memory import config_manager

        config_path = self.plugins_dir.parent / "api_keys.json"
        with (
            patch.object(config_manager, "CONFIG_FILE", config_path),
            patch.object(config_manager, "CONFIG_DIR", self.plugins_dir.parent),
        ):
            digest = "a" * 64
            config_manager.save_plugin_enabled("sample_plugin", True, digest)
            self.assertTrue(config_manager.get_plugin_enabled("sample_plugin", digest))
            self.assertFalse(config_manager.get_plugin_enabled("sample_plugin", "b" * 64))
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["plugin_source_hashes"]["sample_plugin"], digest)

            config_manager.save_plugin_enabled("sample_plugin", False, digest)
            self.assertFalse(config_manager.get_plugin_enabled("sample_plugin", digest))

    def test_plugin_without_top_level_run_is_rejected_without_import(self):
        path = self.plugins_dir / "missing_run.py"
        source_lines = [
            "from pathlib import Path",
            f"Path({str(self.marker)!r}).write_text('executed')",
            "PLUGIN = {'name': 'missing_run', 'description': 'No run', "
            "'parameters': {'type': 'OBJECT', 'properties': {}}}",
        ]
        path.write_text("\\n".join(source_lines) + "\\n", encoding="utf-8")
        registry = plugin_loader.discover_plugins(
            self.plugins_dir, set(), logger=lambda _message: None,
        )
        self.assertFalse(self.marker.exists())
        self.assertFalse(registry.has("missing_run"))
        self.assertFalse(registry.list_for_ui()[0]["valid"])
    def test_symlinked_plugin_source_is_rejected(self):
        source = self._write_plugin(filename="outside.py", name="outside_plugin")
        link = self.plugins_dir / "linked.py"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable in this environment")
        registry = plugin_loader.discover_plugins(
            self.plugins_dir, set(), logger=lambda _message: None,
        )
        linked = next(item for item in registry.list_for_ui() if item["file"] == "linked.py")
        self.assertFalse(linked["valid"])
        self.assertFalse(self.marker.exists())

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
