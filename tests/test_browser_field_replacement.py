"""Approval tests for replacing existing browser field contents."""

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


def _snapshot(value: str, *, password: bool = False, label: str = "Message"):
    return {
        "editable": True,
        "tag": "input",
        "type": "password" if password else "text",
        "password": password,
        "hasValue": bool(value),
        "valueLength": len(value),
        "fingerprint": f"fp-{len(value)}-{sum(map(ord, value))}",
        "value": None if password else value,
        "label": label,
        "id": "",
        "name": label.lower(),
        "placeholder": label,
    }


class FakeField:
    def __init__(self, value: str, *, password: bool = False, label: str = "Message"):
        self.raw_value = value
        self.password = password
        self.label = label
        self.first = self
        self.count = AsyncMock(return_value=1)
        self.clear = AsyncMock(side_effect=self._clear)
        self.type = AsyncMock(side_effect=self._type)
        self._refresh()

    def _refresh(self):
        self.snapshot = _snapshot(
            self.raw_value, password=self.password, label=self.label,
        )

    async def _clear(self):
        self.raw_value = ""
        self._refresh()

    async def _type(self, text: str, delay: int = 0):
        self.raw_value += text
        self._refresh()

    async def evaluate(self, _script, argument=None):
        if argument is not None:
            return self.raw_value == argument
        return dict(self.snapshot)


class FakePage:
    def __init__(self, locators):
        self.url = "https://mail.example.test/compose"
        self.locators = locators

    def is_closed(self):
        return False

    async def title(self):
        return "Example Mail"

    def locator(self, selector):
        return self.locators[selector]

    def get_by_placeholder(self, description, exact=False):
        return self.locators[description]

    def get_by_label(self, description, exact=False):
        return self.locators[description]

    def get_by_role(self, role, name=None):
        return self.locators[name or role]


def make_session(page):
    session = browser_module._BrowserSession.__new__(browser_module._BrowserSession)
    session.run = lambda coroutine: asyncio.run(coroutine)
    session._get_page = AsyncMock(return_value=page)
    return session


class BrowserFieldReplacementTests(unittest.TestCase):
    def _capture_approval(self):
        captured = {}
        patcher = patch.object(
            browser_module.confirm,
            "pending_title",
            return_value="",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        request = patch.object(
            browser_module.confirm,
            "request",
            side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
        )
        request.start()
        self.addCleanup(request.stop)
        return captured

    def test_type_text_shows_existing_and_replacement_text_before_clearing(self):
        field = FakeField("unsent draft")
        page = FakePage({"#message": field})
        captured = self._capture_approval()

        result = asyncio.run(
            make_session(page).type_text("#message", "replacement", clear_first=True)
        )

        self.assertEqual(result, "PENDING")
        field.clear.assert_not_awaited()
        field.type.assert_not_awaited()
        self.assertIn("unsent draft", captured["detail"])
        self.assertIn("replacement", captured["detail"])
        approved = captured["run"]()
        self.assertIn("status=done", approved)
        field.clear.assert_awaited_once()
        field.type.assert_awaited_once_with("replacement", delay=50)
        self.assertEqual(field.raw_value, "replacement")

    def test_changed_field_is_not_replaced_after_approval(self):
        field = FakeField("draft before approval")
        page = FakePage({"#message": field})
        captured = self._capture_approval()

        result = asyncio.run(
            make_session(page).type_text("#message", "new text", clear_first=True)
        )
        self.assertEqual(result, "PENDING")

        field.raw_value = "a newer draft"
        field._refresh()
        refused = captured["run"]()

        self.assertIn("field changed while approval was pending", refused)
        field.clear.assert_not_awaited()
        field.type.assert_not_awaited()
        self.assertEqual(field.raw_value, "a newer draft")

    def test_empty_field_can_be_typed_without_a_confirmation(self):
        field = FakeField("")
        page = FakePage({"#search": field})
        session = make_session(page)

        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(
                session.type_text("#search", "weather today", clear_first=True)
            )

        self.assertIn("status=done", result)
        request.assert_not_called()
        field.clear.assert_not_awaited()
        field.type.assert_awaited_once_with("weather today", delay=50)


    def test_clear_first_false_preserves_existing_field_contents(self):
        field = FakeField("prefix")
        page = FakePage({"#message": field})
        session = make_session(page)

        with patch.object(browser_module.confirm, "request") as request:
            result = asyncio.run(
                session.type_text("#message", "-suffix", clear_first=False)
            )

        self.assertIn("status=done", result)
        request.assert_not_called()
        field.clear.assert_not_awaited()
        self.assertEqual(field.raw_value, "prefix-suffix")

    def test_password_old_and_new_values_are_never_shown_in_confirmation(self):
        field = FakeField("old-secret", password=True, label="Password")
        page = FakePage({"#password": field})
        captured = self._capture_approval()

        result = asyncio.run(
            make_session(page).type_text("#password", "new-secret", clear_first=True)
        )

        self.assertEqual(result, "PENDING")
        self.assertNotIn("old-secret", captured["detail"])
        self.assertNotIn("new-secret", captured["detail"])
        self.assertIn("password value hidden", captured["detail"].lower())
        captured["run"]()
        self.assertEqual(field.raw_value, "new-secret")

    def test_fill_form_requires_one_review_for_all_existing_fields(self):
        name = FakeField("Alex", label="Name")
        message = FakeField("Draft body", label="Message")
        page = FakePage({"#name": name, "#message": message})
        captured = self._capture_approval()
        session = make_session(page)

        result = asyncio.run(session.fill_form({
            "#name": "Jordan",
            "#message": "The final message",
        }))

        self.assertEqual(result, "PENDING")
        name.clear.assert_not_awaited()
        message.clear.assert_not_awaited()
        self.assertIn("Alex", captured["detail"])
        self.assertIn("Jordan", captured["detail"])
        self.assertIn("Draft body", captured["detail"])
        self.assertIn("The final message", captured["detail"])

        approved = captured["run"]()
        self.assertIn("status=done", approved)
        self.assertEqual(name.raw_value, "Jordan")
        self.assertEqual(message.raw_value, "The final message")

    def test_fill_form_rechecks_all_fields_before_changing_any(self):
        name = FakeField("Alex", label="Name")
        message = FakeField("Original draft", label="Message")
        page = FakePage({"#name": name, "#message": message})
        captured = self._capture_approval()
        session = make_session(page)

        self.assertEqual(
            asyncio.run(session.fill_form({"#name": "Jordan", "#message": "Send"})),
            "PENDING",
        )
        message.raw_value = "User edited this after review"
        message._refresh()

        refused = captured["run"]()
        self.assertIn("changed while approval was pending", refused)
        name.clear.assert_not_awaited()
        name.type.assert_not_awaited()
        message.clear.assert_not_awaited()
        message.type.assert_not_awaited()
        self.assertEqual(name.raw_value, "Alex")

    def test_smart_type_uses_the_same_existing_value_gate(self):
        field = FakeField("Existing profile name", label="Name")
        page = FakePage({"Name": field})
        captured = self._capture_approval()

        result = asyncio.run(make_session(page).smart_type("Name", "New profile"))
        self.assertEqual(result, "PENDING")
        self.assertIn("Existing profile name", captured["detail"])
        field.clear.assert_not_awaited()
        captured["run"]()
        self.assertEqual(field.raw_value, "New profile")


if __name__ == "__main__":
    unittest.main()
