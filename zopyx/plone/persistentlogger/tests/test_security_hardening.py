"""Regression tests for browser, packaging, profile and frontend hardening."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ..browser.logger import Logging
from ..browser.retention import Export, Retention

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

    def test_unpaged_entries_endpoint_is_bounded_and_deprecated(self):
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
        upgrade = ROOT / "profiles" / "default" / "upgrades" / "to_2" / "registry.xml"
        self.assertIn('id="zopyx.plone.persistentlogger.ViewAuditLog"', zcml)
        self.assertIn('permission="zopyx.plone.persistentlogger.ViewAuditLog"', zcml)
        self.assertIn('name="View audit log"', rolemap)
        self.assertIn("<version>2</version>", metadata)
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
