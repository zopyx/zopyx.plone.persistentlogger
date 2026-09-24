"""Focused browser security and accessibility regression tests.

These tests intentionally exercise browser-facing contracts rather than
implementation details. The canonical Zope test runner discovers this module
through :func:`test_suite`, as it does the rest of this legacy test package.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ..browser.logger import Logging
from ..browser.retention import Export, Retention
from ..models import RetentionPolicy

PACKAGE_ROOT = Path(__file__).parents[1]


class Response:
    """Minimal response object for browser-view unit tests."""

    def __init__(self):
        self.status = None
        self.headers: dict[str, str] = {}

    def setStatus(self, status, reason=None):
        self.status = status

    def setHeader(self, name, value):
        self.headers[name] = value


class Request:
    """Minimal request object supporting form and query-style reads."""

    def __init__(self, form=None, method="GET"):
        self.form = dict(form or {})
        self.method = method
        self.response = Response()

    def get(self, name, default=None):
        return self.form.get(name, default)


class BrowserSecurityRegressionTests(unittest.TestCase):
    """Keep browser endpoints safe on malformed and unauthenticated input."""

    context = SimpleNamespace()

    def test_retention_preview_rejects_get_without_running_csrf_or_mutation(self):
        request = Request(method="GET")
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"
            ) as check_authenticator,
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository"
            ) as get_repository,
        ):
            payload = json.loads(Retention(self.context, request).preview())

        self.assertEqual(request.response.status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")
        check_authenticator.assert_not_called()
        get_repository.assert_not_called()

    def test_retention_preview_authenticates_post_before_parsing_input(self):
        request = Request({"older_than_days": "not-a-number"}, method="POST")
        repository = MagicMock()
        repository.policy.return_value = RetentionPolicy()
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"
            ) as check_authenticator,
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
        ):
            payload = json.loads(Retention(self.context, request).preview())

        check_authenticator.assert_called_once_with(request)
        self.assertEqual(request.response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_number")

    def test_retention_preview_stops_when_csrf_check_rejects_request(self):
        request = Request(method="POST")
        csrf_error = RuntimeError("missing authenticator")
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator",
                side_effect=csrf_error,
            ) as check_authenticator,
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository"
            ) as get_repository,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing authenticator"):
                Retention(self.context, request).preview()

        check_authenticator.assert_called_once_with(request)
        get_repository.assert_not_called()

    def test_retention_preview_invalid_number_is_controlled_bad_request(self):
        request = Request({"max_entries": "NaN"}, method="POST")
        repository = MagicMock()
        repository.policy.return_value = RetentionPolicy()
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
        ):
            body = Retention(self.context, request).preview()

        payload = json.loads(body)
        self.assertEqual(request.response.status, 400)
        self.assertEqual(request.response.headers["Content-Type"], "application/json")
        self.assertEqual(payload["error"]["code"], "invalid_number")
        self.assertNotIn("Traceback", body)

    def test_retention_delete_invalid_uuid_is_controlled_bad_request(self):
        request = Request({"operation_id": "not-a-uuid"}, method="POST")
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository"
            ) as get_repository,
        ):
            body = Retention(self.context, request).delete()

        payload = json.loads(body)
        self.assertEqual(request.response.status, 400)
        self.assertEqual(request.response.headers["Content-Type"], "application/json")
        self.assertEqual(payload["error"]["code"], "invalid_uuid")
        get_repository.assert_not_called()

    def test_export_invalid_format_is_controlled_bad_request(self):
        request = Request({"format": "xml"})
        with patch(
            "zopyx.plone.persistentlogger.browser.retention.get_repository"
        ) as get_repository:
            body = Export(self.context, request)()

        payload = json.loads(body)
        self.assertEqual(request.response.status, 400)
        self.assertEqual(request.response.headers["Content-Type"], "application/json")
        self.assertEqual(payload["error"]["code"], "invalid_export")
        get_repository.assert_not_called()

    def test_unpaged_legacy_endpoint_is_bounded_or_explicitly_deprecated(self):
        request = Request()
        repository = MagicMock()
        repository.search.return_value = SimpleNamespace(rows=(), total=0)
        view = Logging(self.context, request)
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.logger.get_repository",
                return_value=repository,
            ),
            patch.object(view, "entries", return_value=[]),
        ):
            body = view.entries_json()

        limits = [
            call.kwargs.get("limit")
            for call in repository.search.call_args_list
            if "limit" in call.kwargs
        ]
        bounded = bool(limits) and all(
            isinstance(limit, int) and limit >= 0 for limit in limits
        )
        deprecated = (
            any(
                request.response.headers.get(header)
                for header in ("Deprecation", "Sunset", "Warning")
            )
            or "deprecated" in str(body).lower()
        )
        self.assertTrue(
            bounded or deprecated,
            "legacy unpaged endpoint must be bounded or explicitly deprecated",
        )


class BrowserAccessibilityRegressionTests(unittest.TestCase):
    """Protect the names and announcements used by assistive technology."""

    def test_retention_reason_has_client_side_minimum_length(self):
        markup = (PACKAGE_ROOT / "browser" / "retention.pt").read_text()
        reason = re.search(
            r'<textarea(?=[^>]*(?:id="retention-reason"|name="reason"))'
            r"[^>]*>.*?</textarea>",
            markup,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(reason, "retention reason control is missing")
        assert reason is not None
        self.assertIn('minlength="10"', reason.group(0))

    def test_logger_quick_filter_has_an_accessible_name(self):
        markup = (PACKAGE_ROOT / "browser" / "logger.pt").read_text()
        quick_input = re.search(
            r'<input(?=[^>]*id="persistent-log-quick")[^>]*>',
            markup,
        )
        self.assertIsNotNone(quick_input, "quick filter control is missing")
        assert quick_input is not None
        tag = quick_input.group(0)
        has_aria_name = re.search(r'aria-label="[^"]+"', tag)
        has_explicit_label = re.search(
            r'<label(?=[^>]*for="persistent-log-quick")[^>]*>'
            r".*?Quick filter.*?</label>",
            markup,
            flags=re.DOTALL,
        )
        self.assertTrue(
            has_aria_name or has_explicit_label,
            "quick filter must have a label or aria-label",
        )

    def test_logger_status_is_a_polite_live_region(self):
        markup = (PACKAGE_ROOT / "browser" / "logger.pt").read_text()
        status = re.search(r'<[^>]+id="persistent-log-status"[^>]*>', markup)
        self.assertIsNotNone(status, "logger status element is missing")
        assert status is not None
        tag = status.group(0)
        self.assertIn('role="status"', tag)
        self.assertIn('aria-live="polite"', tag)

    def test_logger_grid_has_an_accessible_name(self):
        markup = (PACKAGE_ROOT / "browser" / "logger.pt").read_text()
        self.assertIn('id="persistent-log-title"', markup)
        role_tags = re.findall(r'<[^>]+role="(?:region|grid)"[^>]*>', markup)
        named_region = any(
            'aria-label="' in tag or 'aria-labelledby="persistent-log-title"' in tag
            for tag in role_tags
        )
        self.assertTrue(
            named_region,
            "the log grid must expose a role and accessible name",
        )

    def test_logger_exposes_initial_loading_and_retry_contract(self):
        markup = (PACKAGE_ROOT / "browser" / "logger.pt").read_text()
        self.assertIn("Loading entries…", markup)
        self.assertIn('aria-describedby="persistent-log-status"', markup)
        self.assertIn('aria-busy="true"', markup)
        self.assertIn('aria-controls="persistent-log-grid"', markup)
        self.assertNotIn('style="height: 70vh; width: 100%"', markup)

    def test_retention_states_and_controls_are_named_for_assistive_technology(self):
        markup = (PACKAGE_ROOT / "browser" / "retention.pt").read_text()
        self.assertIn('id="retention-policy-title"', markup)
        self.assertIn('aria-labelledby="retention-policy-title"', markup)
        self.assertIn('id="retention-preview-title"', markup)
        self.assertIn('aria-labelledby="retention-preview-title"', markup)
        self.assertIn('role="alert"', markup)
        self.assertIn('aria-live="assertive"', markup)
        self.assertIn("No entries match this retention policy.", markup)
        self.assertIn('aria-describedby="retention-reason-help"', markup)


def test_suite():
    """Register every regression class with the canonical Zope runner."""
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    suite.addTest(loader.loadTestsFromTestCase(BrowserSecurityRegressionTests))
    suite.addTest(loader.loadTestsFromTestCase(BrowserAccessibilityRegressionTests))
    return suite
