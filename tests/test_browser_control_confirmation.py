"""Tests for the browser controller's human-approval boundary."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

from actions._browser_safety import requires_form_submission_approval

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

    def test_enter_in_contenteditable_chat_composer_requires_approval(self):
        context = {
            "hasForm": False,
            "isEditable": True,
            "formLabel": "Message",
            "submitters": [],
            "fields": ["Message: This should not send yet"],
        }
        page = FakePage(context)
        captured = {}
        session = make_session()
        session._get_page = AsyncMock(return_value=page)

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.press("Enter"))

        self.assertEqual(result, "PENDING")
        page.keyboard.press.assert_not_awaited()
        self.assertIn("This should not send yet", captured["detail"])

    def test_enter_in_searchbox_does_not_require_approval(self):
        context = {
            "hasForm": False,
            "isEditable": False,
            "isSearch": True,
            "formLabel": "Search",
            "activeControl": "weather",
            "submitters": [],
            "fields": ["Search: weather"],
        }
        page = FakePage(context)
        session = make_session()
        session._get_page = AsyncMock(return_value=page)

        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session.press("Enter"))

        self.assertEqual(result, "Pressed: Enter")
        page.keyboard.press.assert_awaited_once_with("Enter")
        request.assert_not_called()

    def test_enter_in_contenteditable_chat_composer_requires_approval(self):
        context = {
            "hasForm": False, "isEditable": True, "formLabel": "Message",
            "submitters": [],
            "fields": ["Message: This should not send yet"],
        }
        page = FakePage(context)
        captured = {}
        session = make_session()
        session._get_page = AsyncMock(return_value=page)
        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.press("Enter"))
        self.assertEqual(result, "PENDING")
        page.keyboard.press.assert_not_awaited()
        self.assertIn("This should not send yet", captured["detail"])

    def test_enter_in_searchbox_does_not_require_approval(self):
        context = {
            "hasForm": False, "isEditable": False, "isSearch": True,
            "formLabel": "Search", "activeControl": "weather",
            "submitters": [], "fields": ["Search: weather"],
        }
        page = FakePage(context)
        session = make_session()
        session._get_page = AsyncMock(return_value=page)
        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session.press("Enter"))
        self.assertEqual(result, "Pressed: Enter")
        page.keyboard.press.assert_awaited_once_with("Enter")
        request.assert_not_called()


    def test_unknown_browser_button_requires_approval(self):
        locator = FakeLocator({
            "label": "Continue", "formLabel": "", "identifiers": "continue-checkout",
            "isActionControl": True, "isSubmitControl": False, "fields": [],
        })
        page = FakePage()
        captured = {}
        session = make_session()
        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session._click_locator(page, locator, "Continue"))
        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()

    def test_safe_navigation_button_does_not_require_approval(self):
        locator = FakeLocator({
            "label": "Home", "formLabel": "", "identifiers": "home-button",
            "isActionControl": True, "isSubmitControl": False, "fields": [],
        })
        page = FakePage()
        session = make_session()
        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session._click_locator(page, locator, "Home"))
        self.assertIn("Clicked", result)
        locator.click.assert_awaited_once()
        request.assert_not_called()

    def test_enter_on_sensitive_button_outside_form_requires_approval(self):
        context = {
            "hasForm": False, "isEditable": False,
            "activeControl": "Send message", "formLabel": "",
            "submitters": [], "fields": [],
        }
        page = FakePage(context)
        captured = {}
        session = make_session()
        session._get_page = AsyncMock(return_value=page)
        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session.press("Enter"))
        self.assertEqual(result, "PENDING")
        page.keyboard.press.assert_not_awaited()
        self.assertIn("Send message", captured["title"])

    def test_browser_close_all_requires_human_approval(self):
        captured = {}
        with (
            patch.object(browser_module._registry, "list_sessions", return_value="Chrome: 2 tabs"),
            patch.object(browser_module._registry, "close_all") as close_all,
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = browser_module.browser_control({"action": "close_all"})
            self.assertEqual(result, "PENDING")
            close_all.assert_not_called()
            self.assertIn("Chrome: 2 tabs", captured["detail"])
            captured["run"]()
            close_all.assert_called_once()



    def test_browser_close_tab_requires_human_approval(self):
        class FakeSession:
            def __init__(self):
                self.closed = False
            async def get_url(self):
                return "https://mail.example.test/draft"
            async def close_tab(self):
                self.closed = True
                return "closed"
            def run(self, coroutine):
                return asyncio.run(coroutine)

        session = FakeSession()
        captured = {}
        with (
            patch.object(browser_module._registry, "get", return_value=session),
            patch.object(browser_module._registry, "pop_native_url", return_value=""),
            patch.object(
                browser_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            result = browser_module.browser_control({"action": "close_tab"})
            self.assertEqual(result, "PENDING")
            self.assertFalse(session.closed)
            self.assertIn("unsent messages", captured["detail"])
            captured["run"]()
            self.assertTrue(session.closed)

    def test_unsubscribe_button_is_detected_from_selector_identifier(self):
        locator = FakeLocator({
            "label": "Manage notifications",
            "formLabel": "",
            "identifiers": "unsubscribe-button",
            "isActionControl": True,
            "isSubmitControl": False,
            "fields": [],
        })
        page = FakePage()
        captured = {}
        session = make_session()
        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session._click_locator(page, locator, "#unsubscribe-button"))
        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()



    def test_same_site_link_away_from_unsaved_draft_requires_approval(self):
        details = {
            "label": "Open settings",
            "formLabel": "",
            "identifiers": "settings-link",
            "isActionControl": False,
            "isSubmitControl": False,
            "isNavigationLink": True,
            "href": "https://mail.example.test/settings",
            "fields": [],
        }
        dirty = [{
            "label": "Message",
            "type": "textarea",
            "password": False,
            "length": 10,
            "value": "Unsaved!!!",
            "id": "message",
            "name": "message",
        }]
        locator = FakeLocator(details)
        page = FakePage()
        captured = {}
        session = make_session()
        session._page_dirty_snapshot = AsyncMock(return_value=dirty)

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            result = asyncio.run(session._click_locator(page, locator, "Open settings"))

        self.assertEqual(result, "PENDING")
        locator.click.assert_not_awaited()
        self.assertIn("Unsaved!!!", captured["detail"])
        self.assertIn("settings-link", captured["detail"])

    def test_browser_link_approval_refuses_if_draft_changes(self):
        details = {
            "label": "Open settings",
            "formLabel": "",
            "identifiers": "settings-link",
            "isActionControl": False,
            "isSubmitControl": False,
            "isNavigationLink": True,
            "href": "https://mail.example.test/settings",
            "fields": [],
        }
        original = [{"label": "Message", "value": "before", "password": False}]
        changed = [{"label": "Message", "value": "after", "password": False}]
        locator = FakeLocator(details)
        page = FakePage()
        captured = {}
        session = make_session()
        dirty_check = AsyncMock(side_effect=[original, changed])
        session._page_dirty_snapshot = dirty_check

        with patch.object(
            browser_module.confirm, "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        ):
            self.assertEqual(
                asyncio.run(session._click_locator(page, locator, "Open settings")),
                "PENDING",
            )
        refused = captured["run"]()
        self.assertIn("unsaved form data changed", refused)
        locator.click.assert_not_awaited()

    def test_same_document_fragment_link_does_not_prompt(self):
        locator = FakeLocator({
            "label": "Jump to details",
            "formLabel": "",
            "identifiers": "",
            "isActionControl": False,
            "isSubmitControl": False,
            "isNavigationLink": True,
            "href": "#details",
            "fields": [],
        })
        page = FakePage()
        session = make_session()
        session._page_dirty_snapshot = AsyncMock(
            return_value=[{"label": "Draft", "value": "text"}]
        )
        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(session._click_locator(page, locator, "Jump to details"))
        self.assertIn("Clicked", result)
        locator.click.assert_awaited_once()
        session._page_dirty_snapshot.assert_not_awaited()
        request.assert_not_called()

    def test_non_http_browser_link_scheme_is_blocked(self):
        locator = FakeLocator({
            "label": "Email support",
            "formLabel": "",
            "identifiers": "",
            "isActionControl": False,
            "isSubmitControl": False,
            "isNavigationLink": True,
            "href": "mailto:support@example.test",
            "fields": [],
        })
        page = FakePage()
        session = make_session()
        result = asyncio.run(session._click_locator(page, locator, "Email support"))
        self.assertIn("status=failed", result)
        locator.click.assert_not_awaited()

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
