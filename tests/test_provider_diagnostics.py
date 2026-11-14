from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from providers import diagnostics, registry


class FakeProvider:
    def __init__(self, probe=None, caps=None, **catalogues):
        self.probe_result = probe
        self.caps = caps or {}
        for name, value in catalogues.items():
            setattr(self, name, value)

    def probe(self):
        if isinstance(self.probe_result, Exception):
            raise self.probe_result
        return self.probe_result


class ProviderDiagnosticsTests(unittest.TestCase):
    def test_probe_returns_health_and_vision_without_touching_ui(self):
        provider = FakeProvider((True, "connected"), {"vision": True})
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.probe_provider(fake_registry, "test-llm", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "connected")
        self.assertIs(result["vision"], True)

    def test_probe_redacts_api_keys_from_provider_errors(self):
        secret = "super-secret-provider-token"
        provider = FakeProvider(RuntimeError(f"401 for key {secret}"))
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.probe_provider(
            fake_registry, "hosted-llm", {"api_key": secret})

        self.assertFalse(result["ok"])
        self.assertIn("[redacted]", result["message"])
        self.assertNotIn(secret, result["message"])

    def test_provider_without_probe_is_reported_as_not_checkable(self):
        provider = object()
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.probe_provider(fake_registry, "plain", {})

        self.assertIsNone(result["ok"])
        self.assertIn("Nothing to check", result["message"])

    def test_model_options_keep_value_and_human_label_pairs(self):
        provider = FakeProvider(model_options=lambda: [
            ("qwen-model", "Qwen Model · FREE"),
            ("other", "Other Model"),
            ("", "invalid"),
            ("malformed",),
            None,
        ])
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.list_provider_options(fake_registry, "llm", {})

        self.assertEqual(result["field"], "model")
        self.assertEqual(result["items"], [
            ("qwen-model", "Qwen Model · FREE"),
            ("other", "Other Model"),
        ])
        self.assertFalse(result["error"])

    def test_voice_catalogue_is_detected_and_errors_are_redacted(self):
        secret = "voice-secret-token"
        provider = FakeProvider(voices=lambda: (_ for _ in ()).throw(
            RuntimeError(f"request rejected: {secret}")))
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.list_provider_options(
            fake_registry, "tts", {"access_token": secret})

        self.assertEqual(result["field"], "")
        self.assertTrue(result["error"])
        self.assertIn("[redacted]", result["message"])
        self.assertNotIn(secret, result["message"])

    def test_local_model_lists_are_normalised(self):
        provider = FakeProvider(models=lambda: ["qwen2.5:3b", "llama3.2"])
        fake_registry = type("R", (), {
            "build": staticmethod(lambda _pid, _cfg: provider)
        })

        result = diagnostics.list_provider_options(fake_registry, "ollama", {})

        self.assertEqual(result["items"], [
            ("qwen2.5:3b", "qwen2.5:3b"),
            ("llama3.2", "llama3.2"),
        ])

    def test_concurrent_provider_build_loads_a_heavy_instance_only_once(self):
        calls = []
        instance = object()

        def factory(_cfg):
            calls.append(threading.get_ident())
            time.sleep(0.04)
            return instance

        decl = {
            "id": "heavy_test_provider",
            "label": "Heavy test provider",
            "factory": factory,
            "caps": {},
            "_available": True,
            "requires": (),
        }
        key = registry._fingerprint("heavy_test_provider", {"model": "base"})
        with registry._instances_lock:
            old = registry._instances.pop(key, None)
        try:
            with patch.object(registry, "get_declaration", return_value=decl):
                with ThreadPoolExecutor(max_workers=10) as pool:
                    results = list(pool.map(
                        lambda _n: registry.build(
                            "heavy_test_provider", {"model": "base"}),
                        range(20),
                    ))
            self.assertTrue(all(result is instance for result in results))
            self.assertEqual(len(calls), 1)
        finally:
            with registry._instances_lock:
                registry._instances.pop(key, None)
                if old is not None:
                    registry._instances[key] = old


if __name__ == "__main__":
    unittest.main()
