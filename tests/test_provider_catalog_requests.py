import unittest
from concurrent.futures import ThreadPoolExecutor

from providers.catalog_requests import CatalogRequestGate


class CatalogRequestGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = CatalogRequestGate()

    def test_duplicate_request_for_same_configuration_is_coalesced(self):
        first = self.gate.begin("xkiro", "key-a/model-a")
        second = self.gate.begin("xkiro", "key-a/model-a")

        self.assertEqual(first, 1)
        self.assertIsNone(second)
        self.assertTrue(self.gate.is_current("xkiro", first, "key-a/model-a"))

    def test_concurrent_duplicate_requests_start_only_one_worker(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            generations = list(pool.map(
                lambda _n: self.gate.begin("groq", "endpoint/model"),
                range(24),
            ))

        self.assertEqual(sum(value is not None for value in generations), 1)
        self.assertEqual(sum(value is None for value in generations), 23)

    def test_new_configuration_supersedes_old_worker(self):
        old = self.gate.begin("xkiro", "key-a/model-a")
        new = self.gate.begin("xkiro", "key-b/model-b")

        self.assertEqual(new, old + 1)
        self.assertFalse(self.gate.is_current("xkiro", old, "key-a/model-a"))
        self.assertTrue(self.gate.is_current("xkiro", new, "key-b/model-b"))
        self.assertFalse(self.gate.complete("xkiro", old, "key-a/model-a"))
        self.assertTrue(self.gate.is_current("xkiro", new, "key-b/model-b"))
        self.assertTrue(self.gate.complete("xkiro", new, "key-b/model-b"))

    def test_invalidation_rejects_a_result_and_allows_retry(self):
        old = self.gate.begin("whisper", "model=small")
        self.gate.invalidate("whisper")
        self.assertFalse(self.gate.is_current("whisper", old, "model=small"))

        retry = self.gate.begin("whisper", "model=small")
        self.assertGreater(retry, old)
        self.assertFalse(self.gate.complete("whisper", old, "model=small"))
        self.assertTrue(self.gate.is_current("whisper", retry, "model=small"))

    def test_invalidate_all_clears_every_provider_request(self):
        llm = self.gate.begin("llm", "endpoint-a")
        tts = self.gate.begin("tts", "voice-a")
        self.gate.invalidate_all()

        self.assertFalse(self.gate.is_current("llm", llm, "endpoint-a"))
        self.assertFalse(self.gate.is_current("tts", tts, "voice-a"))
        self.assertIsNotNone(self.gate.begin("llm", "endpoint-a"))
        self.assertIsNotNone(self.gate.begin("tts", "voice-a"))


if __name__ == "__main__":
    unittest.main()
