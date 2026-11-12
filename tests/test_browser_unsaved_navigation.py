"""Tests for browser navigation protection around unsaved form data."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

_real_find_spec = importlib.util.find_spec


def _find_spec(name, *args, **kwargs):
    if name == "playwright":
        return object()
    return _real_find_spec(name, *args, **kwargs)


with patch("importlib.util.find_spec", side_effect=_find_spec):
    browser_module = importlib.import_module("actions.browser_control")


class FakeNavigationPage:
    def __init__(self, snapshots):
        self.url = "https://mail.example.test/draft"
        self._snapshots = list(snapshots)
        self.goto = AsyncMock(side_effect=self._goto)
        self.last_target = None

    def is_closed(self):
        return False

    async def title(self):
        return "Example Mail"

    async def evaluate(self, _script):
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]

    async def _goto(self, url, **kwargs):
        self.last_target = url
        self.url = url


class FakeContext:
    def __init__(self, page):
        self.pages = [page]

    async def new_page(self):
        page = FakeNavigationPage([[]])
        self.pages.append(page)
        return page


def make_session(page):
    session = browser_module._BrowserSession.__new__(browser_module._BrowserSession)
    session.run = lambda coroutine: asyncio.run(coroutine)
    session._get_page = AsyncMock(return_value=page)
    session._context = FakeContext(page)
    session.profile_kind = "real"
    session.browser_name = "chrome"
    return session


class BrowserUnsavedNavigationTests(unittest.TestCase):
    def test_navigation_asks_before_leaving_unsaved_message(self):
        dirty = [{
            "label": "Message",
            "type": "textarea",
            "password": False,
            "length": 16,
            "value": "Unsent draft text",
            "id": "message",
            "name": "message",
        }]
        page = FakeNavigationPage([dirty, dirty])
        session = make_session(page)
        captured = {}

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.go_to("https://example.com"))

        self.assertEqual(result, "PENDING")
        page.goto.assert_not_awaited()
        self.assertIn("Unsent draft text", captured["detail"])
        self.assertIn("Destination: https://example.com", captured["detail"])
        approved = captured["run"]()
        self.assertIn("status=done", approved)
        self.assertEqual(page.last_target, "https://example.com")

    def test_changed_draft_is_not_abandoned_after_approval(self):
        original = [{
            "label": "Message", "type": "textarea", "password": False,
            "length": 5, "value": "draft", "id": "message", "name": "message",
        }]
        changed = [{
            "label": "Message", "type": "textarea", "password": False,
            "length": 11, "value": "newer draft", "id": "message", "name": "message",
        }]
        page = FakeNavigationPage([original, changed])
        session = make_session(page)
        captured = {}

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            self.assertEqual(
                asyncio.run(session.go_to("https://example.com")),
                "PENDING",
            )

        refused = captured["run"]()
        self.assertIn("changed while approval was pending", refused)
        page.goto.assert_not_awaited()

    def test_clean_page_navigates_without_extra_confirmation(self):
        page = FakeNavigationPage([[]])
        session = make_session(page)

        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session.go_to("https://example.com"))

        self.assertIn("status=done", result)
        page.goto.assert_awaited_once()
        request.assert_not_called()

    def test_selected_upload_file_is_treated_as_unsaved_form_data(self):
        dirty = [{
            "label": "Attachment",
            "type": "file",
            "password": False,
            "length": 1,
            "value": "Selected files: contract.pdf",
            "id": "attachment",
            "name": "attachment",
        }]
        page = FakeNavigationPage([dirty, dirty])
        session = make_session(page)
        captured = {}

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.go_to("https://example.com"))

        self.assertEqual(result, "PENDING")
        self.assertIn("contract.pdf", captured["detail"])
        page.goto.assert_not_awaited()

    def test_password_values_are_redacted_from_navigation_approval(self):
        dirty = [{
            "label": "Password", "type": "password", "password": True,
            "length": 10, "value": None, "id": "password", "name": "password",
        }]
        page = FakeNavigationPage([dirty, dirty])
        session = make_session(page)
        captured = {}

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.go_to("https://example.com"))

        self.assertEqual(result, "PENDING")
        self.assertIn("password value hidden", captured["detail"].lower())
        self.assertNotIn("super-secret", captured["detail"])


if __name__ == "__main__":
    unittest.main()
