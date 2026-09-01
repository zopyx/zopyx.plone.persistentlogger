"""Temporary integration check for the retention GUI template rendering."""

import unittest

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

    def test_logger_table_renders(self):
        from AccessControl.SecurityManagement import newSecurityManager

        from zopyx.plone.persistentlogger.api import log_event

        portal = self.layer["portal"]
        user = portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(portal.acl_users))
        log_event(portal, "render check entry", level="warning")
        view = portal.restrictedTraverse("@@persistent-log")
        html = view()
        self.assertIn("Logging", html)
        self.assertIn("render check entry", html)
        self.assertIn("text-bg-warning", html)
        self.assertIn("persistent-log-export?format=json", html)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(RenderCheck)
