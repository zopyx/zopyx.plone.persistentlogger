"""Focused M5-M8 authorization, scope, quota, and hold tests."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ..browser.api import AuditAPI, AuditExportAPI, HoldAPI
from ..data_subject import (
    DataSubjectQuery,
    QuotaExceeded,
    create_hold,
    export_journal,
    release_hold,
    search_data_subject,
)
from ..models import DeletionPreview
from ..retention import RetentionService


class Request:
    def __init__(self, form=None, method="GET"):
        self.form = dict(form or {})
        self.method = method
        self.status = None
        self.headers = {}
        self.response = SimpleNamespace(
            setStatus=self.set_status, setHeader=self.set_header
        )

    def get(self, name, default=None):
        return self.form.get(name, default)

    def set_status(self, value, reason=None):
        self.status = value

    def set_header(self, name, value):
        self.headers[name] = value


class APIAndScopeTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace()
        self.repository = MagicMock()
        self.repository.events.return_value = [
            {
                "event_id": str(uuid4()),
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "actor": "alice",
                "target": "subject-1",
                "comment": "visible",
                "severity": "info",
                "details": None,
            },
            {
                "event_id": str(uuid4()),
                "created_at": datetime(2026, 1, 2, tzinfo=UTC),
                "actor": "bob",
                "target": "subject-2",
                "comment": "other object record",
                "severity": "info",
                "details": None,
            },
        ]
        self.repository.journal.return_value = []

    def test_data_subject_search_is_explicitly_scoped_and_exact(self):
        other = SimpleNamespace()
        other_repository = MagicMock()
        other_repository.events.return_value = [
            dict(self.repository.events.return_value[0], comment="other object")
        ]
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                side_effect=[self.repository, other_repository],
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                side_effect=["object-one", "object-two"],
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.is_held",
                return_value=False,
            ),
        ):
            result = search_data_subject(
                [self.context, other], DataSubjectQuery(actor="ALICE")
            )
        self.assertEqual(result.total, 2)
        self.assertEqual(
            [row["object_uid"] for row in result.rows], ["object-one", "object-two"]
        )
        self.assertEqual(result.objects_scanned, 2)

    def test_api_enforces_view_permission_and_object_quota(self):
        request = Request({"limit": "1001"})
        context = SimpleNamespace(
            portal_membership=SimpleNamespace(checkPermission=lambda *_: True)
        )
        with patch(
            "zopyx.plone.persistentlogger.browser.api.get_repository",
            return_value=self.repository,
        ):
            payload = json.loads(AuditAPI(context, request)())
        self.assertEqual(request.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_query")

        denied = Request()
        context.portal_membership.checkPermission = lambda *_: False
        payload = json.loads(AuditAPI(context, denied)())
        self.assertEqual(denied.status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

        out_of_range = Request({"offset": "999", "limit": "2"})
        context.portal_membership.checkPermission = lambda *_: True
        payload = json.loads(AuditAPI(context, out_of_range)())
        self.assertEqual(out_of_range.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_query")

    def test_api_fails_closed_when_permission_lookup_is_unavailable(self):
        request = Request()
        payload = json.loads(AuditAPI(SimpleNamespace(), request)())
        self.assertEqual(request.status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_browser_routes_use_separate_audit_permissions(self):
        zcml = (Path(__file__).parents[1] / "browser" / "configure.zcml").read_text()
        rolemap = (
            Path(__file__).parents[1] / "profiles" / "default" / "rolemap.xml"
        ).read_text()
        for permission in (
            "ViewAuditLog",
            "ExportAuditLog",
            "ManageAuditRetention",
            "ManageAuditHold",
            "CreateAuditDemo",
        ):
            self.assertIn(f"zopyx.plone.persistentlogger.{permission}", zcml)
        self.assertIn('name="View audit log"', rolemap)
        self.assertIn('name="Export audit log"', rolemap)
        self.assertIn('<role name="Auditor" />', rolemap)
        self.assertEqual(rolemap.count('<role name="Auditor" />'), 1)

    def test_export_requires_its_separate_permission(self):
        request = Request()
        context = SimpleNamespace(
            portal_membership=SimpleNamespace(
                checkPermission=lambda permission, *_: permission.endswith(
                    "ExportAuditLog"
                )
            )
        )
        with patch(
            "zopyx.plone.persistentlogger.browser.api.export_journal",
            return_value=b'{"records": []}',
        ):
            payload = AuditExportAPI(context, request)()
        self.assertEqual(payload, b'{"records": []}')
        self.assertEqual(request.headers["Content-Type"], "application/json")

    def test_site_quota_is_hard_and_export_does_not_truncate(self):
        other = SimpleNamespace()
        repository = MagicMock()
        repository.events.return_value = self.repository.events.return_value
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="object-one",
            ),
        ):
            with self.assertRaises(QuotaExceeded):
                search_data_subject(
                    [self.context, other], DataSubjectQuery(), site_limit=1
                )

            repository.journal.return_value = [{"event_id": "one"}, {"event_id": "two"}]
            with self.assertRaises(QuotaExceeded):
                export_journal([self.context], limit=1)

    def test_hold_permission_is_checked_before_csrf(self):
        request = Request(method="POST")
        context = SimpleNamespace(
            portal_membership=SimpleNamespace(checkPermission=lambda *_: False)
        )
        with patch(
            "zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"
        ) as check:
            payload = json.loads(HoldAPI(context, request)())
        self.assertEqual(request.status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")
        check.assert_not_called()


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace()
        self.annotations = {}
        self.repository = MagicMock()
        self.repository.record_governance.return_value = {}

    def test_hold_lifecycle_is_journaled_and_release_is_required(self):
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.IAnnotations",
                return_value=self.annotations,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=self.repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="obj",
            ),
        ):
            hold = create_hold(
                self.context, "manager", "data subject request", event_ids=["event-1"]
            )
            self.assertEqual(hold.event_ids, ("event-1",))
            self.assertEqual(len(self.repository.record_governance.call_args_list), 1)
            released = release_hold(
                self.context, hold.hold_id, "manager", "request completed"
            )
        self.assertIsNotNone(released.released_at)
        self.assertEqual(self.repository.record_governance.call_count, 2)

    def test_retention_refuses_a_hold_even_after_preview(self):
        preview = DeletionPreview(
            uuid4(), "obj", datetime.now(UTC), (uuid4(),), "digest"
        )
        repository = MagicMock()
        with patch("zopyx.plone.persistentlogger.retention.is_held", return_value=True):
            with self.assertRaisesRegex(ValueError, "legal hold"):
                RetentionService(self.context, repository).execute(
                    preview, "retention cleanup with hold", "manager"
                )
        repository.delete_and_journal.assert_not_called()


def test_suite():
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    suite.addTest(loader.loadTestsFromTestCase(APIAndScopeTests))
    suite.addTest(loader.loadTestsFromTestCase(HoldTests))
    return suite


if __name__ == "__main__":
    unittest.main()
