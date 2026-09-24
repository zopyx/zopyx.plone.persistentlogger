"""End to end checks: the Plone audit log writing into PostgreSQL.

The storage contract suite verifies the RDBMS backend in isolation.  This
module verifies the other half of the story: with the *Audit log storage*
control panel set to ``rdbms``, the ordinary Plone code paths -- the audit
subscribers, the retention service and the integrity journal -- really do
write into the relational database, and the ZODB annotations stay untouched.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import transaction
from plone.registry.interfaces import IRegistry
from sqlmodel import Session, func, select
from zope.annotation.interfaces import IAnnotations
from zope.component import getUtility
from zope.interface import alsoProvides
from zope.lifecycleevent import modified

from ..audit import audit_settings_changed
from ..browser.retention import RetentionGUI
from ..interfaces import (
    BrowserLayer,
    IAuditLoggingSettings,
    IStorageSettings,
)
from ..models import LogEvent, RetentionPolicy, Severity
from ..retention import RetentionService
from ..storage import (
    BACKEND_RDBMS,
    BACKEND_ZODB,
    LOG_KEY,
    AnnotationRepository,
    get_repository,
    object_uid,
)
from ..storage import factory as storage_factory
from ..storage.rdbms import EventRecord, GovernanceRecord, SQLRepository, get_engine
from .base import POLICY_INTEGRATION_TESTING
from .postgres import database_url


class RdbmsAuditIntegrationTests(unittest.TestCase):
    """The audit log written to PostgreSQL through the regular Plone hooks."""

    layer = POLICY_INTEGRATION_TESTING

    def setUp(self):
        # Starts (or reuses) the Postgres test container; skips the test when
        # no database is available.
        self.url = database_url()

        portal = self.layer["portal"]
        self._login(portal)
        registry = getUtility(IRegistry)
        settings = registry.forInterface(IStorageSettings, check=False)
        audit = registry.forInterface(IAuditLoggingSettings, check=False)
        previous = (
            settings.backend,
            settings.database_url,
            audit.enabled,
            list(audit.content_types),
        )
        self.addCleanup(self.restore_configuration, previous)

        settings.backend = BACKEND_RDBMS
        settings.database_url = self.url
        audit.enabled = True
        audit.content_types = []
        storage_factory.clear_cache()
        audit_settings_changed()

        alsoProvides(portal.REQUEST, BrowserLayer)
        self.engine = get_engine(self.url)
        self.addCleanup(transaction.abort)
        self.addCleanup(storage_factory.clear_cache)

    # -- helpers -----------------------------------------------------------
    def _login(self, portal):
        from AccessControl.SecurityManagement import newSecurityManager

        user = portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(portal.acl_users))

    def restore_configuration(self, previous):
        """Leave the site configuration as this test found it."""
        backend, database_url_value, enabled, content_types = previous
        registry = getUtility(IRegistry)
        settings = registry.forInterface(IStorageSettings, check=False)
        audit = registry.forInterface(IAuditLoggingSettings, check=False)
        settings.backend = backend
        settings.database_url = database_url_value
        audit.enabled = enabled
        audit.content_types = content_types
        storage_factory.clear_cache()
        audit_settings_changed()

    def repository(self, context=None):
        return get_repository(context if context is not None else self.portal)

    def event_rows(self, uid):
        with Session(self.engine) as session:
            return session.scalar(
                select(func.count())
                .select_from(EventRecord)
                .where(EventRecord.object_uid == uid)
            )

    def governance_rows(self, uid):
        with Session(self.engine) as session:
            return session.scalar(
                select(func.count())
                .select_from(GovernanceRecord)
                .where(GovernanceRecord.object_uid == uid)
            )

    @property
    def portal(self):
        return self.layer["portal"]

    # -- the configured backend drives the subscribers ---------------------
    def test_create_and_edit_are_written_to_postgres(self):
        obj = self.portal[
            self.portal.invokeFactory("Event", "pg-audit", title="Before")
        ]
        uid = object_uid(obj)

        repository = self.repository(obj)
        self.assertIsInstance(repository, SQLRepository)
        self.assertEqual(self.event_rows(uid), 1)

        events = repository.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "create")
        self.assertEqual(events[0]["details"]["title"], "Before")

        # ... and the ZODB annotations of the object stay untouched.
        self.assertNotIn(LOG_KEY, IAnnotations(obj))

        obj.title = "After"
        modified(obj)
        events = repository.events()
        self.assertEqual(len(events), 2)
        edit = next(entry for entry in events if entry["event_type"] == "edit")
        self.assertEqual(edit["details"]["changes"]["title"]["old"], "Before")
        self.assertEqual(edit["details"]["changes"]["title"]["new"], "After")
        self.assertEqual(self.event_rows(uid), 2)

    def test_records_survive_an_aborted_content_transaction(self):
        obj = self.portal[
            self.portal.invokeFactory("Event", "pg-abort", title="Rolled back")
        ]
        uid = object_uid(obj)
        self.assertEqual(self.event_rows(uid), 1)

        # The RDBMS backend commits audit records immediately, so they are
        # not rolled back together with the content that produced them.
        transaction.abort()
        self.assertEqual(self.event_rows(uid), 1)

    def test_switching_back_to_zodb_writes_annotations_again(self):
        settings = getUtility(IRegistry).forInterface(IStorageSettings, check=False)
        settings.backend = BACKEND_ZODB
        storage_factory.clear_cache()

        obj = self.portal[
            self.portal.invokeFactory("Event", "zodb-audit", title="Local")
        ]
        uid = object_uid(obj)
        repository = self.repository(obj)
        self.assertIsInstance(repository, AnnotationRepository)
        self.assertEqual(len(repository.events()), 1)
        self.assertEqual(self.event_rows(uid), 0)

    # -- retention and governance journal ----------------------------------
    def test_retention_deletes_rows_and_journals_in_postgres(self):
        uid = object_uid(self.portal)
        repository = self.repository()
        repository.clear()
        repository.append(self.event(days_ago=31, comment="old one"))
        repository.append(self.event(days_ago=30, comment="old two"))
        repository.append(self.event(days_ago=1, comment="recent"))
        self.assertEqual(self.event_rows(uid), 3)

        service = RetentionService(self.portal, repository)
        service.set_policy(
            RetentionPolicy(enabled=True, older_than_days=7, max_entries=10),
            "manager",
            "turn on retention",
        )
        preview = service.preview(service.repository.policy())
        self.assertEqual(len(preview.event_ids), 2)
        result = service.execute(preview, "retention policy cleanup", "manager")

        self.assertEqual((result.eligible, result.deleted, result.missing), (2, 2, 0))
        self.assertEqual(self.event_rows(uid), 1)
        self.assertEqual(
            [entry["comment"] for entry in repository.events()], ["recent"]
        )
        # The governance journal is persisted in PostgreSQL as well.
        self.assertEqual(self.governance_rows(uid), 2)
        self.assertEqual(
            [entry["action"] for entry in repository.journal()],
            ["retention_policy_changed", "retention_delete"],
        )
        self.assertEqual(repository.journal()[-1]["deleted"], 2)

    def test_retention_gui_works_on_the_rdbms_backend(self):
        uid = object_uid(self.portal)
        repository = self.repository()
        repository.clear()
        repository.append(self.event(days_ago=90, comment="ancient"))
        self.assertEqual(self.event_rows(uid), 1)

        request = self.portal.REQUEST
        request.method = "POST"
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            request.form.clear()
            request.form.update(
                {
                    "action": "save-policy",
                    "enabled": "1",
                    "older_than_days": "30",
                    "max_entries": "10",
                }
            )
            view = RetentionGUI(self.portal, request)
            view()
            self.assertEqual(view.messages, [("info", "Retention policy saved.")])
            self.assertEqual(repository.policy().older_than_days, 30)

            request.form.clear()
            request.form.update({"action": "preview"})
            view = RetentionGUI(self.portal, request)
            view()
            self.assertEqual(len(view.preview_events), 1)
            # the preview action stores the operation id in the form
            operation_id = request.form["operation_id"]

            request.form.clear()
            request.form.update(
                {
                    "action": "delete",
                    "operation_id": operation_id,
                    "reason": "retention policy cleanup",
                }
            )
            view = RetentionGUI(self.portal, request)
            view()

        self.assertEqual(view.messages[-1][0], "info")
        self.assertEqual(self.event_rows(uid), 0)
        self.assertEqual(repository.journal()[-1]["action"], "retention_delete")

    # -- shared machinery --------------------------------------------------
    def event(self, days_ago, comment):
        from datetime import timedelta

        from ..models import utc_now

        return LogEvent(
            comment=comment,
            created_at=utc_now() - timedelta(days=days_ago),
            severity=Severity.INFO,
            actor="manager",
            event_type="application",
            target="portal",
            details={"source": "test"},
        )


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(RdbmsAuditIntegrationTests)
