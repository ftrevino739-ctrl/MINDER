"""Tests for the browser controller's human-approval boundary."""

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


class FakeLocator:
    def __init__(self, details):
        self.details = details
        self.first = self
        self.click = AsyncMock()
        self.count = AsyncMock(return_value=1)

    async def evaluate(self, _script):
        return dict(self.details)


class FakeKeyboard:
    def __init__(self):
        self.press = AsyncMock()


class FakePage:
    def __init__(self, context=None):
        self.url = "https://mail.example.test/compose"
        self._context = context or {"formLabel": "", "submitters": [], "fields": []}
        self.keyboard = FakeKeyboard()
        self.evaluate = AsyncMock(side_effect=lambda _script: dict(self._context))

    def is_closed(self):
        return False

    async def title(self):
        return "Example Mail"


def make_session():
    session = browser_module._BrowserSession.__new__(browser_module._BrowserSession)
    session.run = lambda coroutine: asyncio.run(coroutine)
    return session


class BrowserConfirmationTests(unittest.TestCase):
    def test_send_button_waits_for_human_approval_and_shows_form_values(self):
        details = {
            "label": "Send",
            "formLabel": "Compose message",
            "fields": ["To: alex@example.test", "Message: Meet at 6."],
        }
        locator = FakeLocator(details)
        page = FakePage()
        captured = {}

        with (
            patch.object(browser_module.confirm, "pending_title", return_value=""),
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = asyncio.run(make_session()._click_locator(page, locator, "Send"))

        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()
        self.assertEqual(captured["title"], "Approve browser action: Send")
        self.assertIn("alex@example.test", captured["detail"])
        self.assertIn("Meet at 6.", captured["detail"])

    def test_confirmed_click_is_refused_if_form_changes(self):
        details = {"label": "Send", "formLabel": "Compose", "fields": ["Message: original"]}
        locator = FakeLocator(details)
        page = FakePage()
        captured = {}
        session = make_session()
        session._get_page = AsyncMock(return_value=page)

        with (
            patch.object(browser_module.confirm, "pending_title", return_value=""),
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            asyncio.run(session._click_locator(page, locator, "Send"))

        locator.details["fields"] = ["Message: changed"]
        result = captured["run"]()
        self.assertIn("changed while approval was pending", result)
        locator.click.assert_not_awaited()

    def test_exactly_reviewed_click_runs_only_after_human_confirmation(self):
        details = {"label": "Pay now", "formLabel": "Checkout", "fields": ["Total: $20"]}
        locator = FakeLocator(details)
        page = FakePage()
        captured = {}
        session = make_session()

        with (
            patch.object(browser_module.confirm, "pending_title", return_value=""),
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = asyncio.run(session._click_locator(page, locator, "Pay now"))

        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()
        approved_result = captured["run"]()
        self.assertIn("status=unverified", approved_result)
        locator.click.assert_awaited_once()

    def test_enter_submission_also_requires_approval(self):
        context = {
            "hasForm": True,
            "formLabel": "Message composer",
            "submitters": ["Send"],
            "fields": ["Recipient: Alex", "Message: Hello there"],
        }
        page = FakePage(context)
        captured = {}
        session = make_session()
        session._get_page = AsyncMock(return_value=page)

        with (
            patch.object(browser_module.confirm, "pending_title", return_value=""),
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = asyncio.run(session.press("Enter"))

        self.assertEqual(result, "PENDING")
        page.keyboard.press.assert_not_awaited()
        self.assertIn("Hello there", captured["detail"])
        self.assertIn("Approve browser submission: Send", captured["title"])


    def test_unlabelled_submit_control_still_requires_approval(self):
        locator = FakeLocator({
            "label": "",
            "formLabel": "",
            "isSubmitControl": True,
            "fields": ["Query: private text"],
        })
        page = FakePage()
        captured = {}
        session = make_session()

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session._click_locator(page, locator, "button[type=submit]"))

        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()
        self.assertIn("private text", captured["detail"])

    def test_unknown_form_submission_requires_approval_but_plain_search_does_not(self):
        self.assertTrue(requires_form_submission_approval("", ["Continue"]))
        self.assertTrue(requires_form_submission_approval("", []))
        self.assertFalse(requires_form_submission_approval("search-form", ["Search"]))
        self.assertFalse(requires_form_submission_approval("", ["Buscar"]))

    def test_ordinary_navigation_click_does_not_prompt(self):
        locator = FakeLocator({"label": "Home", "formLabel": "", "fields": []})
        page = FakePage()
        session = make_session()

        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session._click_locator(page, locator, "Home"))

        self.assertIn("Clicked", result)
        locator.click.assert_awaited_once()
        request.assert_not_called()

    def test_unsafe_scheme_is_rejected_before_native_browser_launch(self):
        with patch.object(browser_module, "_open_native") as open_native:
            result = browser_module.browser_control({
                "action": "go_to",
                "url": "file:///C:/Users/Private/document.html",
            })

        self.assertIn("status=failed", result)
        open_native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
