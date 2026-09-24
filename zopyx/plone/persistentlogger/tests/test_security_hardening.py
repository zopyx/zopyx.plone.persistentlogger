"""Regression tests for browser, packaging, profile and frontend hardening."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ..browser.integrity import IntegrityHealthView
from ..browser.logger import Logging
from ..browser.retention import Export, Retention, RetentionGUI
from ..models import RetentionPolicy

ROOT = Path(__file__).parents[1]


class Response:
    def __init__(self):
        self.status = None
        self.headers = {}

    def setStatus(self, status, reason=None):
        self.status = status

    def setHeader(self, name, value):
        self.headers[name] = value


class Request:
    def __init__(self, form=None, method="GET"):
        self.form = dict(form or {})
        self.method = method
        self.response = Response()

    def get(self, name, default=None):
        return self.form.get(name, default)


class BrowserHardeningTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace()

    def test_retention_preview_is_post_only_and_csrf_checked(self):
        request = Request(method="GET")
        with patch(
            "zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"
        ) as check:
            payload = json.loads(Retention(self.context, request).preview())
        self.assertEqual(request.response.status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")
        check.assert_not_called()

    def test_retention_preview_rejects_malformed_numeric_input(self):
        request = Request({"older_than_days": "not-a-number"}, method="POST")
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            payload = json.loads(Retention(self.context, request).preview())
        self.assertEqual(request.response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_number")

    def test_retention_preview_rejects_repeated_numeric_input(self):
        request = Request({"older_than_days": ["30", "60"]}, method="POST")
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            payload = json.loads(Retention(self.context, request).preview())
        self.assertEqual(request.response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_number")

    def test_retention_preview_maps_disabled_policy_to_structured_bad_request(self):
        request = Request(method="POST")
        repository = MagicMock()
        repository.policy.return_value = RetentionPolicy()
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
        ):
            payload = json.loads(Retention(self.context, request).preview())
        self.assertEqual(request.response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_policy")
        self.assertEqual(payload["error"]["message"], "retention policy is disabled")

    def test_export_limit_is_checked_before_materializing_rows(self):
        request = Request({"format": "json"})
        repository = MagicMock()
        rows = MagicMock()
        rows.__iter__.side_effect = AssertionError("rows were materialized")
        repository.search.return_value = SimpleNamespace(rows=rows, total=2)
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.MAX_EXPORT_ENTRIES",
                1,
            ),
        ):
            payload = json.loads(Export(self.context, request)())
        self.assertEqual(request.response.status, 413)
        self.assertEqual(payload["error"]["code"], "export_limit")
        repository.search.assert_called_once_with(limit=2)
        rows.__iter__.assert_not_called()

    def test_export_byte_limit_is_a_payload_too_large_response(self):
        request = Request({"format": "json"})
        repository = MagicMock()
        repository.search.return_value = SimpleNamespace(rows=({},), total=1)
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.MAX_EXPORT_BYTES",
                1,
            ),
        ):
            payload = json.loads(Export(self.context, request)())
        self.assertEqual(request.response.status, 413)
        self.assertEqual(payload["error"]["code"], "export_limit")

    def test_repeated_gui_values_are_normalized_or_rejected(self):
        repository = MagicMock()
        repository.policy.return_value = RetentionPolicy()
        with (
            patch.object(RetentionGUI, "template", MagicMock(return_value="rendered")),
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.plone.api.user.get_current",
                return_value=SimpleNamespace(getUserName=lambda: "manager"),
            ),
        ):
            request = Request(
                {
                    "action": "save-policy",
                    "enabled": ["1"],
                    "older_than_days": ["30"],
                    "max_entries": ["2"],
                },
                method="POST",
            )
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(view.messages, [("info", "Retention policy saved.")])

            request = Request(
                {
                    "action": "save-policy",
                    "older_than_days": ["30", "31"],
                    "max_entries": ["2"],
                },
                method="POST",
            )
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
        self.assertEqual(request.response.status, 400)
        self.assertEqual(
            view.messages, [("error", "older_than_days must be provided once")]
        )

        request = Request(
            {"action": ["save-policy", "preview"], "older_than_days": ["30"]},
            method="POST",
        )
        with (
            patch.object(RetentionGUI, "template", MagicMock(return_value="rendered")),
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
        ):
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
        self.assertEqual(request.response.status, 400)
        self.assertEqual(view.messages, [("error", "action must be provided once")])

    def test_retention_delete_rejects_malformed_uuid(self):
        request = Request({"operation_id": "not-a-uuid"}, method="POST")
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            payload = json.loads(Retention(self.context, request).delete())
        self.assertEqual(request.response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_uuid")
    def test_export_rejects_unknown_format_as_structured_bad_request(self):
        request = Request({"format": "xml"})
        payload = Export(self.context, request)()
        self.assertEqual(request.response.status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_export")

    def test_integrity_health_is_read_only_and_safe_on_backend_failure(self):
        request = Request(method="POST")
        with patch(
            "zopyx.plone.persistentlogger.browser.integrity.verify_repository"
        ) as verify:
            payload = json.loads(IntegrityHealthView(self.context, request)())
        self.assertEqual(request.response.status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")
        verify.assert_not_called()

        request = Request()
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.integrity.get_repository",
                side_effect=RuntimeError("database token leaked"),
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.integrity.verify_repository"
            ) as verify,
        ):
            payload = json.loads(IntegrityHealthView(self.context, request)())
        self.assertEqual(request.response.status, 500)
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertNotIn("token", json.dumps(payload).lower())
        verify.assert_not_called()

        request = Request()
        repository = MagicMock()
        repository.search.return_value = SimpleNamespace(rows=(), total=2)
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.logger.get_repository",
                return_value=repository,
            ),
            patch("zopyx.plone.persistentlogger.browser.logger.MAX_LEGACY_ENTRIES", 1),
        ):
            payload = json.loads(Logging(self.context, request).entries_json())
        self.assertEqual(request.response.status, 413)
        self.assertEqual(payload["error"]["code"], "legacy_endpoint_limit")
        self.assertEqual(request.response.headers["Deprecation"], "true")
        repository.search.assert_called_once_with(limit=2)


class StaticHardeningTests(unittest.TestCase):
    def test_templates_expose_accessible_grid_and_retention_controls(self):
        logger = (ROOT / "browser" / "logger.pt").read_text()
        retention = (ROOT / "browser" / "retention.pt").read_text()
        script = (ROOT / "browser" / "resources" / "persistent-log.js").read_text()
        self.assertIn('aria-labelledby="persistent-log-title"', logger)
        self.assertIn('aria-live="polite"', logger)
        self.assertIn('aria-label="Quick filter entries"', logger)
        self.assertIn('minlength="10"', retention)
        self.assertIn("<caption", retention)
        self.assertIn('scope="col"', retention)
        self.assertIn("Try again", script)
        self.assertIn("Loading entries", script)
        self.assertIn("No entries found", script)

    def test_audit_view_permission_and_profile_upgrade_are_declared(self):
        zcml = (ROOT / "browser" / "configure.zcml").read_text()
        rolemap = (ROOT / "profiles" / "default" / "rolemap.xml").read_text()
        metadata = (ROOT / "profiles" / "default" / "metadata.xml").read_text()
        upgrade = ROOT / "profiles" / "default" / "upgrades" / "to_3" / "registry.xml"
        self.assertIn('id="zopyx.plone.persistentlogger.ViewAuditLog"', zcml)
        self.assertIn('permission="zopyx.plone.persistentlogger.ViewAuditLog"', zcml)
        self.assertIn('name="View audit log"', rolemap)
        self.assertIn("<version>3</version>", metadata)
        self.assertTrue(upgrade.is_file())

    def test_packaging_uses_odfpy_only_as_the_ods_extra(self):
        pyproject = tomllib.loads((ROOT.parents[2] / "pyproject.toml").read_text())
        self.assertNotIn("ods", pyproject["project"]["dependencies"])
        self.assertEqual(
            pyproject["project"]["optional-dependencies"]["ods"], ["odfpy>=1.4"]
        )

    def test_unused_datatables_tree_is_not_shipped(self):
        self.assertFalse((ROOT / "browser" / "resources" / "DataTables").exists())
        manifest = (ROOT.parents[2] / "MANIFEST.in").read_text()
        self.assertIn("*.js", manifest)


def test_suite():
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    suite.addTest(loader.loadTestsFromTestCase(BrowserHardeningTests))
    suite.addTest(loader.loadTestsFromTestCase(StaticHardeningTests))
    return suite


if __name__ == "__main__":
    unittest.main()
