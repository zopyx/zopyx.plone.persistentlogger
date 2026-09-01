"""Unit tests for the governance domain, repository, and exporters."""

from __future__ import annotations

import csv
import io
import json
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

from persistent import Persistent

from zopyx.plone.persistentlogger.api import export_log, log_event
from zopyx.plone.persistentlogger.browser.retention import Export as BrowserExport
from zopyx.plone.persistentlogger.browser.retention import Retention as BrowserRetention
from zopyx.plone.persistentlogger.exports import export_events
from zopyx.plone.persistentlogger.exports.ods import render_ods
from zopyx.plone.persistentlogger.exports.xlsx import render_xlsx
from zopyx.plone.persistentlogger.models import (
    ExportRequest,
    LogEvent,
    RetentionPolicy,
    Severity,
)
from zopyx.plone.persistentlogger.repository import (
    AnnotationRepository,
    _event_date,
    object_uid,
)
from zopyx.plone.persistentlogger.retention import RetentionService
from zopyx.plone.persistentlogger.serialization import (
    canonical_json,
    event_digest,
    json_default,
)


class Context(Persistent):
    __name__ = "context"

    def absolute_url(self):
        return "https://example.test/context"


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        self.annotation_store = {}
        self.annotation_patch = patch(
            "zopyx.plone.persistentlogger.repository.IAnnotations",
            return_value=self.annotation_store,
        )
        self.annotation_patch.start()
        self.repository = AnnotationRepository(self.context)
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def tearDown(self):
        self.annotation_patch.stop()

    def event(self, created_at=None, comment="event"):
        return LogEvent(
            comment=comment,
            severity=Severity.INFO,
            actor="manager",
            event_type="governance",
            target="context",
            details={"source": "test"},
            created_at=created_at or self.now,
        )

    def test_models_and_serialization_edge_cases(self):
        with self.assertRaises(ValueError):
            LogEvent(comment="x", event_type="x" * 101, created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="x", actor="x" * 256, created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="x", target="x" * 2049, created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="x", info_url="x" * 2049, created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="x", details={"data": "x" * 65537}, created_at=self.now)
        with self.assertRaises(ValueError):
            RetentionPolicy(older_than_days=0)
        with self.assertRaises(ValueError):
            RetentionPolicy(max_entries=101)
        with self.assertRaises(ValueError):
            ExportRequest("xml")
        with self.assertRaises(ValueError):
            ExportRequest("json", max_entries=0)
        with self.assertRaises(ValueError):
            ExportRequest("json", max_bytes=0)
        with self.assertRaises(ValueError):
            ExportRequest("json", max_bytes=1_000_000_001)

        self.assertEqual(json_default(datetime(2026, 1, 1)), "2026-01-01T00:00:00")
        self.assertEqual(json_default(date(2026, 1, 1)), "2026-01-01")
        uid = uuid4()
        self.assertEqual(json_default(uid), str(uid))
        self.assertEqual(json_default(Severity.ERROR), "error")
        self.assertEqual(json_default({"b", "a"}), ["a", "b"])
        with self.assertRaises(TypeError):
            json_default(object())
        self.assertIn('"value":1', canonical_json({"value": 1}))

    def test_repository_policy_legacy_and_missing_delete(self):
        self.assertEqual(object_uid(self.context), "https://example.test/context")
        with patch("plone.uuid.interfaces.IUUID", return_value="resolved-uid"):
            self.assertEqual(object_uid(self.context), "resolved-uid")
        self.repository.annotations["invalid"] = "not an event"
        self.assertEqual(self.repository.events(), [])
        del self.repository.annotations["invalid"]
        self.assertFalse(self.repository.policy().enabled)
        configured = RetentionPolicy(enabled=True, older_than_days=30, max_entries=2)
        self.repository.set_policy(configured)
        self.assertEqual(self.repository.policy(), configured)
        legacy_id = uuid4()
        self.repository.annotations[datetime(2020, 1, 1)] = {
            "uuid": str(legacy_id),
            "date": datetime(2020, 1, 1),
            "comment": "legacy",
        }
        self.assertIsNotNone(self.repository.get(str(legacy_id)))
        self.assertIn(str(legacy_id), self.repository.annotations)
        self.assertFalse(
            any(key == datetime(2020, 1, 1) for key in self.repository.annotations)
        )
        self.assertEqual(
            _event_date({"date": "invalid"}), datetime.min.replace(tzinfo=UTC)
        )
        preview = self.repository.preview_delete(configured, self.now)
        del self.repository.annotations[str(legacy_id)]
        result = self.repository.delete_preview(preview, "remove obsolete legacy event")
        self.assertEqual((result.deleted, result.missing), (0, 1))
        first = self.repository.record_governance(
            "export", "manager", "export requested"
        )
        second = self.repository.record_governance(
            "export", "manager", "export requested"
        )
        self.assertEqual(second["previous_digest"], first["integrity_digest"])

        with self.assertRaises(ValueError):
            LogEvent(comment="", created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="event", created_at=datetime(2026, 1, 1))
        first = self.event()
        second = self.event(comment="second")
        self.assertNotEqual(event_digest(first), event_digest(second))

    def test_repository_append_preview_delete_and_journal(self):
        old = self.event(self.now - timedelta(days=400), "old")
        newest = self.event(self.now, "new")
        self.repository.append(newest)
        self.repository.append(old)
        self.assertEqual(len(self.repository.events()), 2)
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365, max_entries=100),
            self.now,
        )
        self.assertEqual(preview.event_ids, (old.event_id,))
        with self.assertRaises(ValueError):
            self.repository.delete_preview(preview, "short")
        result = self.repository.delete_preview(preview, "retention policy cleanup")
        self.assertEqual((result.deleted, result.missing), (1, 0))
        journal = self.repository.record_governance(
            "delete", "manager", "retention policy cleanup", deleted=1
        )
        self.assertEqual(journal["action"], "delete")
        self.assertTrue(journal["integrity_digest"])
        self.assertEqual(len(self.repository.events()), 1)

    def test_stale_preview_is_rejected(self):
        old = self.event(self.now - timedelta(days=400))
        self.repository.append(old)
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True), self.now
        )
        self.repository.journal()[str(preview.operation_id)] = preview
        preview = replace(preview, selection_digest="changed")
        with self.assertRaises(ValueError):
            self.repository.delete_preview(preview, "retention policy cleanup")

    def test_public_api(self):
        with patch(
            "zopyx.plone.persistentlogger.api.plone.api.user.get_current",
            return_value=MagicMock(getUserName=MagicMock(return_value="manager")),
        ):
            entry = log_event(self.context, "API event", level="info")
        self.assertEqual(entry["comment"], "API event")
        self.assertEqual(
            json.loads(export_log(self.context, "json"))["records"],
            [
                {
                    "actor": "manager",
                    "comment": "API event",
                    "created_at": entry["created_at"].isoformat(),
                    "details": None,
                    "event_id": entry["event_id"],
                    "event_type": "application",
                    "info_url": None,
                    "integrity_digest": entry["integrity_digest"],
                    "schema_version": 1,
                    "severity": "info",
                    "target": "",
                }
            ],
        )

        event = self.event(comment="=formula")
        json_data = export_events([event], "json")
        payload = json.loads(json_data)
        self.assertEqual(payload["records"][0]["comment"], "=formula")
        csv_data = export_events([event], "csv").decode("utf-8")
        row = next(csv.DictReader(io.StringIO(csv_data)))
        self.assertEqual(row["comment"], "'=formula")
        self.assertIn('"source":"test"', row["details"])

    def test_browser_preview_respects_disabled_policy(self):
        request = type("Request", (), {"form": {}, "method": "GET"})()
        with self.assertRaises(ValueError):
            BrowserRetention(self.context, request).preview()

        response = MagicMock()
        get_request = type("Request", (), {"method": "GET", "response": response})()
        self.assertEqual(
            BrowserRetention(self.context, get_request).delete(), "POST required"
        )
        response.setStatus.assert_called_once_with(405)

        post_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "form": {"operation_id": str(uuid4())},
                "response": response,
            },
        )()
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.IAnnotations",
                return_value=self.annotation_store,
            ),
        ):
            self.assertEqual(
                BrowserRetention(self.context, post_request).delete(),
                "deletion preview is missing or stale",
            )
        response.setStatus.assert_called_with(400)

        service = RetentionService(self.context, self.repository)
        with self.assertRaises(ValueError):
            service.preview(RetentionPolicy())
        policy = RetentionPolicy(enabled=True, older_than_days=30, max_entries=1)
        service.set_policy(policy, "manager", "enable retention policy")
        self.assertEqual(self.repository.policy(), policy)
        old = self.event(self.now - timedelta(days=31), "old")
        self.repository.append(old)
        preview_request = type(
            "Request",
            (),
            {
                "form": {"older_than_days": "30", "max_entries": "1"},
                "method": "GET",
            },
        )()
        preview_view = BrowserRetention(self.context, preview_request)
        preview_payload = json.loads(preview_view.preview())
        self.assertEqual(preview_payload["event_ids"], [str(old.event_id)])
        preview = self.repository.preview_delete(policy, self.now)
        delete_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "form": {
                    "operation_id": str(preview.operation_id),
                    "reason": "manual retention cleanup",
                },
                "response": MagicMock(),
            },
        )()
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.IAnnotations",
                return_value=self.annotation_store,
            ),
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.plone.api.user.get_current",
                return_value=MagicMock(getUserName=MagicMock(return_value="manager")),
            ),
        ):
            result = json.loads(BrowserRetention(self.context, delete_request).delete())
        self.assertEqual(result["deleted"], 1)

        export_request = type(
            "Request",
            (),
            {"form": {"format": "json"}, "response": MagicMock()},
        )()
        export_data = BrowserExport(self.context, export_request)()
        self.assertEqual(json.loads(export_data)["records"], [])
        export_request.response.setHeader.assert_any_call(
            "Content-Type", "application/json"
        )

        event = self.event()
        xlsx_data = export_events([event], "xlsx")
        with ZipFile(io.BytesIO(xlsx_data)) as archive:
            self.assertIn("xl/workbook.xml", archive.namelist())
        ods_data = export_events([event], "ods")
        with ZipFile(io.BytesIO(ods_data)) as archive:
            self.assertIn("content.xml", archive.namelist())

    def test_optional_export_dependencies_report_clear_errors(self):
        original_import = __import__("builtins").__import__

        def blocked_import(name, *args, **kwargs):
            if name in {"openpyxl", "odf.opendocument", "odf.table", "odf.text"}:
                raise ImportError(name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocked_import):
            with self.assertRaisesRegex(RuntimeError, "XLSX"):
                render_xlsx([])
            with self.assertRaisesRegex(RuntimeError, "ODS"):
                render_ods([])

        event = self.event()
        with self.assertRaises(ValueError):
            export_events([event], "unknown")
        with self.assertRaises(ValueError):
            export_events([event], "json", max_entries=0)
        with self.assertRaises(ValueError):
            export_events([event], "json", max_bytes=1)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(GovernanceTests)
