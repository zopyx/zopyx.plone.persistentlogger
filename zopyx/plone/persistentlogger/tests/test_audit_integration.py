"""Integration checks for the audit logging control panel and subscribers."""

import unittest

import transaction
from plone.registry.interfaces import IRegistry
from zope.component import getUtility
from zope.interface import alsoProvides
from zope.lifecycleevent import modified

from zopyx.plone.persistentlogger.interfaces import (
    BrowserLayer,
    IAuditLoggingSettings,
)
from zopyx.plone.persistentlogger.repository import AnnotationRepository
from zopyx.plone.persistentlogger.tests.base import POLICY_INTEGRATION_TESTING


class AuditIntegrationTests(unittest.TestCase):
    layer = POLICY_INTEGRATION_TESTING

    def _login(self, portal):
        from AccessControl.SecurityManagement import newSecurityManager

        user = portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(portal.acl_users))

    def _enable_audit(self, portal, content_types=()):
        registry = getUtility(IRegistry)
        settings = registry.forInterface(IAuditLoggingSettings, check=False)
        settings.enabled = True
        settings.content_types = list(content_types)

    def test_control_panel_view_registered(self):
        portal = self.layer["portal"]
        self._login(portal)
        view = portal.restrictedTraverse("@@audit-logging-settings")
        html = view()
        self.assertIn("Audit logging", html)
        self.assertIn("Audit logging enabled", html)

    def test_subscriber_logs_create_and_edit(self):
        portal = self.layer["portal"]
        self._login(portal)
        request = portal.REQUEST
        alsoProvides(request, BrowserLayer)
        self._enable_audit(portal)

        obj = portal[portal.invokeFactory("Event", "audit-event", title="Before")]
        repository = AnnotationRepository(obj)
        events = sorted(repository.events(), key=lambda e: e["created_at"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "create")
        self.assertEqual(events[0]["details"]["title"], "Before")

        obj.title = "After"
        modified(obj)
        events = sorted(repository.events(), key=lambda e: e["created_at"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["event_type"], "edit")
        self.assertEqual(events[1]["details"]["changes"]["title"]["old"], "Before")
        self.assertEqual(events[1]["details"]["changes"]["title"]["new"], "After")
        transaction.abort()

    def test_subscriber_respects_content_type_filter(self):
        portal = self.layer["portal"]
        self._login(portal)
        self._enable_audit(portal, content_types=["Document"])

        obj = portal[portal.invokeFactory("Event", "filtered-event", title="Skip me")]
        self.assertEqual(len(AnnotationRepository(obj).events()), 0)

        doc = portal[portal.invokeFactory("Document", "filtered-doc", title="Audit me")]
        self.assertEqual(len(AnnotationRepository(doc).events()), 1)
        transaction.abort()


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(AuditIntegrationTests)
