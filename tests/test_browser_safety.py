"""Regression tests for browser URL and high-impact action safeguards."""
from __future__ import annotations
import unittest
from actions._browser_safety import normalize_browser_url, requires_human_approval


class BrowserSafetyTests(unittest.TestCase):
    def test_normalizes_common_web_addresses(self):
        self.assertEqual(normalize_browser_url("youtube"), "https://youtube.com")
        self.assertEqual(normalize_browser_url("example.com/path"), "https://example.com/path")
        self.assertEqual(normalize_browser_url("http://localhost:8080"), "http://localhost:8080")
        self.assertEqual(normalize_browser_url("https://127.0.0.1:3000"), "https://127.0.0.1:3000")

    def test_allows_only_http_and_https_schemes(self):
        for address in (
            "file:///C:/Users/Private/document.html", "javascript:alert(1)",
            "data:text/html,hello", "blob:https://example.com/id",
            "chrome://settings", "edge://downloads", "ftp://example.com/file",
        ):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    normalize_browser_url(address)

    def test_rejects_embedded_credentials_and_malformed_hosts(self):
        for address in ("https://user:pass@example.com", "https://", "https://bad host"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    normalize_browser_url(address)

    def test_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            normalize_browser_url("https://example.com\r\nHost: attacker")

    def test_preserves_internal_blank_page(self):
        self.assertEqual(normalize_browser_url(""), "about:blank")
        self.assertEqual(normalize_browser_url("about:blank"), "about:blank")

    def test_detects_sensitive_actions_in_english_and_spanish(self):
        for label in ("Send message", "Submit", "Place order", "Delete account",
                      "Publicar", "Pagar ahora", "Enviar mensaje", "Cancelar reserva"):
            with self.subTest(label=label):
                self.assertTrue(requires_human_approval(label))

    def test_ignores_ordinary_navigation_controls(self):
        for label in ("Home", "Open menu", "Next page", "Search", "Learn more"):
            with self.subTest(label=label):
                self.assertFalse(requires_human_approval(label))


if __name__ == "__main__":
    unittest.main()
