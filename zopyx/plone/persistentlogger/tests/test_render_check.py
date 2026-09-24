"""Temporary integration check for the retention GUI template rendering."""

import unittest
from pathlib import Path

from zopyx.plone.persistentlogger.models import RetentionPolicy
from zopyx.plone.persistentlogger.retention import RetentionService
from zopyx.plone.persistentlogger.tests.base import (
    POLICY_INTEGRATION_TESTING,
)


class RenderCheck(unittest.TestCase):
    layer = POLICY_INTEGRATION_TESTING

    def test_retention_gui_renders(self):
        from AccessControl.SecurityManagement import newSecurityManager

        portal = self.layer["portal"]
        user = portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(portal.acl_users))
        service = RetentionService(portal)
        service.set_policy(
            RetentionPolicy(enabled=True, older_than_days=30, max_entries=2),
            "manager",
            "enable for render test",
        )
        view = portal.restrictedTraverse("@@persistent-log-retention")
        html = view()
        self.assertIn("Retention", html)
        self.assertIn('name="older_than_days"', html)
        self.assertIn("Show deletable entries", html)
        # The delete form is only rendered when a preview is active.
        self.assertNotIn('name="operation_id"', html)

        # Preview flow: POST with action=preview renders the delete form.
        import re

        token = re.search(r'name="_authenticator" value="([^"]+)"', html).group(1)
        request = portal.REQUEST
        request.form["action"] = "preview"
        request.form["_authenticator"] = token
        request.method = "POST"
        from zope.interface import alsoProvides

        from zopyx.plone.persistentlogger.interfaces import BrowserLayer

        alsoProvides(request, BrowserLayer)
        html = view()
        self.assertIn('name="action" value="delete"', html)
        self.assertIn('name="operation_id"', html)

    def test_logger_grid_renders(self):
        from AccessControl.SecurityManagement import newSecurityManager

        from zopyx.plone.persistentlogger.api import log_event

        portal = self.layer["portal"]
        user = portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(portal.acl_users))
        log_event(portal, "render check entry", level="warning")
        view = portal.restrictedTraverse("@@persistent-log")
        html = view()
        self.assertIn("Logging", html)
        self.assertIn("persistent-log-export?format=json", html)
        # the grid renders client side from the server side data source
        self.assertIn("persistent-log-grid", html)
        self.assertIn("ag-theme-quartz", html)
        self.assertIn("persistent-log-quick", html)
        self.assertIn("window.PERSISTENT_LOGGER_CONFIG", html)
        self.assertIn("persistent-log-data", html)
        # no entries are rendered into the page any more
        self.assertNotIn("render check entry", html)
        self.assertNotIn("datatables.js", html)

    def test_grid_datasource_uses_the_infinite_row_model_api(self):
        """Guard the agGrid contract of the shipped data source.

        The grid runs in infinite row model mode, so agGrid (32.x) hands the
        page ``successCallback(rows, lastRow)``/``failCallback()``.  The server
        side row model's ``success()``/``fail()`` callbacks are not part of that
        params object -- calling them aborts the first request with
        ``TypeError: params.success is not a function`` and the grid stays
        empty.
        """
        source = (
            Path(__file__).parent.parent / "browser" / "resources" / "persistent-log.js"
        ).read_text(encoding="utf-8")
        self.assertIn("params.successCallback(", source)
        self.assertIn("params.failCallback(", source)
        self.assertNotIn("params.success(", source)
        self.assertNotIn("params.fail(", source)
        # the row count of the server side result is handed to the grid
        self.assertIn("result.data.lastRow", source)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(RenderCheck)
