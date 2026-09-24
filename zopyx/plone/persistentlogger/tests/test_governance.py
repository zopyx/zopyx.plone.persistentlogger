"""Unit tests for the governance domain, exporters and browser views.

The storage backends themselves are covered by the contract suite that runs
against both backends (``test_storage_zodb``/``test_storage_rdbms``); this
module covers the domain objects, the serialization helpers, the exporters
and the manager facing browser views, which are backend agnostic.
"""

from __future__ import annotations

import csv
import inspect
import io
import json
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

from persistent import Persistent

from zopyx.plone.persistentlogger.api import (
    execute_retention,
    export_log,
    log_event,
    preview_retention,
)
from zopyx.plone.persistentlogger.browser.retention import (
    Export as BrowserExport,
)
from zopyx.plone.persistentlogger.browser.retention import (
    Retention as BrowserRetention,
)
from zopyx.plone.persistentlogger.browser.retention import RetentionGUI
from zopyx.plone.persistentlogger.exports import export_events
from zopyx.plone.persistentlogger.exports.ods import render_ods
from zopyx.plone.persistentlogger.exports.xlsx import render_xlsx
from zopyx.plone.persistentlogger.models import (
    ExportRequest,
    LogEvent,
    RetentionPolicy,
    Severity,
)
from zopyx.plone.persistentlogger.retention import (
    RetentionExecutionError,
    RetentionService,
)
from zopyx.plone.persistentlogger.serialization import (
    canonical_json,
    event_digest,
    event_row,
    json_default,
)
from zopyx.plone.persistentlogger.storage import (
    AnnotationRepository,
    StorageConfigurationError,
    event_date,
    get_repository,
    object_uid,
)
from zopyx.plone.persistentlogger.storage import factory as storage_factory


class Context(Persistent):
    __name__ = "context"

    def absolute_url(self):
        return "https://example.test/context"


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        self.annotation_store = {}
        self.annotation_patch = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=self.annotation_store,
        )
        self.annotation_patch.start()
        self.addCleanup(storage_factory.clear_cache)
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

        with self.assertRaises(ValueError):
            LogEvent(comment="", created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(comment="event", created_at=datetime(2026, 1, 1))
        first = self.event()
        second = self.event(comment="second")
        self.assertNotEqual(event_digest(first), event_digest(second))

    def test_events_normalize_severity_and_redact_json_details(self):
        event = LogEvent(
            comment="security event",
            severity=" WARNING ",
            created_at=self.now,
            details={
                "password": "do-not-store",
                "nested": [{"api_token": "also-secret", "safe": (1, 2)}],
            },
        )
        self.assertEqual(event.severity, Severity.WARNING)
        self.assertEqual(
            event.details,
            {
                "password": "[REDACTED]",
                "nested": [
                    {"api_token": "[REDACTED]", "safe": [1, 2]},
                ],
            },
        )
        row = event_row(event)
        self.assertEqual(row["details"], event.details)
        self.assertEqual(row["severity"], "warning")
        exported = export_events(
            [
                {
                    "uuid": str(uuid4()),
                    "date": self.now,
                    "comment": "legacy payload",
                    "details_raw": {"password": "legacy-secret"},
                }
            ],
            "json",
        ).decode("utf-8")
        self.assertIn("[REDACTED]", exported)
        self.assertNotIn("legacy-secret", exported)
        with self.assertRaises(ValueError):
            LogEvent(comment="event", severity="not-a-severity", created_at=self.now)
        with self.assertRaises(ValueError):
            LogEvent(
                comment="event",
                details={"unsupported": object()},
                created_at=self.now,
            )
        parameters = inspect.signature(LogEvent).parameters
        self.assertNotIn("request_id", parameters)
        self.assertNotIn("ip_address", parameters)
        self.assertNotIn("user_agent", parameters)

    def test_object_uid_and_legacy_event_dates(self):
        self.assertEqual(object_uid(self.context), "https://example.test/context")
        with patch("plone.uuid.interfaces.IUUID", return_value="resolved-uid"):
            self.assertEqual(object_uid(self.context), "resolved-uid")
        self.assertEqual(
            event_date({"date": "invalid"}), datetime.min.replace(tzinfo=UTC)
        )
        self.assertEqual(
            event_date({"created_at": datetime(2026, 1, 1)}),
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_repository_handles_legacy_and_missing_events(self):
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
        # Reads are side-effect free; explicit migrations, not lookup, normalize
        # legacy annotation keys.
        self.assertIn(datetime(2020, 1, 1), self.repository.annotations)

        preview = self.repository.preview_delete(configured, self.now)
        del self.repository.annotations[datetime(2020, 1, 1)]
        result = self.repository.delete_preview(preview, "remove obsolete legacy event")
        self.assertEqual((result.deleted, result.missing), (0, 1))

    def test_repository_appends_and_chains_digests(self):
        older = self.event(self.now - timedelta(days=400), "old")
        newest = self.event(self.now, "new")
        self.repository.append(older)
        self.repository.append(newest)
        self.assertEqual(
            [entry["comment"] for entry in self.repository.events()], ["old", "new"]
        )
        # Records are chained in the canonical (chronological) order, so
        # each record links to the one before it.
        first, second = self.repository.events()
        self.assertEqual(first["previous_digest"], "")
        self.assertEqual(second["previous_digest"], first["integrity_digest"])
        self.assertNotEqual(first["integrity_digest"], second["integrity_digest"])

        journal = self.repository.record_governance(
            "delete", "manager", "retention policy cleanup", deleted=1
        )
        self.assertEqual(journal["action"], "delete")
        self.assertTrue(journal["integrity_digest"])

    def test_stale_preview_is_rejected(self):
        self.repository.append(self.event(self.now - timedelta(days=400)))
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True), self.now
        )
        stale = replace(preview, selection_digest="changed")
        with self.assertRaises(ValueError):
            self.repository.delete_preview(stale, "retention policy cleanup")

    def test_get_repository_honours_the_configured_backend(self):
        settings = MagicMock(backend="zodb", database_url="")
        repository = get_repository(self.context, settings=settings)
        self.assertIsInstance(repository, AnnotationRepository)
        with (
            patch.dict("os.environ", {"ZOPYX_PERSISTENTLOGGER_DATABASE_URL": ""}),
            self.assertRaises(StorageConfigurationError),
        ):
            get_repository(
                self.context, settings=MagicMock(backend="rdbms", database_url="")
            )
        with self.assertRaises(StorageConfigurationError):
            get_repository(self.context, settings=MagicMock(backend="unknown"))

    def test_runtime_registry_errors_do_not_switch_to_zodb(self):
        storage_factory.clear_cache()
        with patch(
            "zopyx.plone.persistentlogger.storage.factory.getUtility",
            side_effect=RuntimeError("registry unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                storage_factory.storage_settings()

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

    def test_public_export_api_limits_before_loading_all_events(self):
        repository = MagicMock()
        repository.search.return_value = SimpleNamespace(rows=(), total=2)
        export_request = SimpleNamespace(
            format="json", max_entries=1, max_bytes=1_000_000_000
        )
        with (
            patch(
                "zopyx.plone.persistentlogger.api.ExportRequest",
                return_value=export_request,
            ),
            patch(
                "zopyx.plone.persistentlogger.api.get_repository",
                return_value=repository,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "entry limit"):
                export_log(self.context, "json")
        repository.search.assert_called_once_with(limit=2)
        repository.events.assert_not_called()

    def test_public_export_api_does_not_accept_limit_overrides(self):
        parameters = inspect.signature(export_log).parameters
        self.assertNotIn("kwargs", parameters)
        self.assertNotIn("max_entries", parameters)
        self.assertNotIn("max_bytes", parameters)

    def test_browser_preview_respects_disabled_policy(self):
        request = type(
            "Request",
            (),
            {"form": {}, "method": "POST", "response": MagicMock()},
        )()
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            payload = json.loads(BrowserRetention(self.context, request).preview())
        request.response.setStatus.assert_called_with(400)
        self.assertEqual(payload["error"]["code"], "invalid_policy")

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
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
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
                "method": "POST",
                "response": MagicMock(),
            },
        )()
        with patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"):
            preview_payload = json.loads(
                BrowserRetention(self.context, preview_request).preview()
            )
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

    def test_retention_gui_workflow(self):
        def make_request(form, method="GET"):
            return type(
                "Request", (), {"form": form, "method": method, "response": MagicMock()}
            )()

        with (
            patch.object(RetentionGUI, "template", MagicMock(return_value="rendered")),
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.plone.api.user.get_current",
                return_value=MagicMock(getUserName=MagicMock(return_value="manager")),
            ),
        ):
            # GET renders the page with the default policy
            view = RetentionGUI(self.context, make_request({}))
            self.assertEqual(view(), "rendered")
            self.assertEqual(view.messages, [])
            self.assertIsNone(view.preview)
            self.assertEqual(view.preview_events, [])
            self.assertFalse(view.policy.enabled)

            # save-policy persists the form values
            request = make_request(
                {
                    "action": "save-policy",
                    "enabled": "1",
                    "older_than_days": "30",
                    "max_entries": "2",
                },
                "POST",
            )
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(self.repository.policy().older_than_days, 30)
            self.assertTrue(self.repository.policy().enabled)
            self.assertEqual(view.messages, [("info", "Retention policy saved.")])

            # preview with eligible entries sets operation_id and lists events
            old = self.event(self.now - timedelta(days=31), "old")
            self.repository.append(old)
            request = make_request({"action": "preview"}, "POST")
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(len(view.preview_events), 1)
            self.assertTrue(request.form["operation_id"])

            # delete executes the stored preview
            request = make_request(
                {
                    "action": "delete",
                    "operation_id": request.form["operation_id"],
                    "reason": "manual retention cleanup",
                },
                "POST",
            )
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(len(self.repository.events()), 0)
            self.assertEqual(
                view.messages, [("info", "Deleted 1 entries (0 missing).")]
            )

            # delete without a preview reports an error
            request = make_request(
                {"action": "delete", "reason": "manual retention cleanup"}, "POST"
            )
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(
                view.messages,
                [("error", "deletion preview is missing or stale")],
            )

            # preview with a disabled policy reports an error
            request = make_request(
                {"action": "save-policy", "older_than_days": "30", "max_entries": "2"},
                "POST",
            )
            RetentionGUI(self.context, request)()
            request = make_request({"action": "preview"}, "POST")
            view = RetentionGUI(self.context, request)
            self.assertEqual(view(), "rendered")
            self.assertEqual(view.messages, [("error", "retention policy is disabled")])

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
            self.assertIs(blocked_import("json"), original_import("json"))

        event = self.event()
        with self.assertRaises(ValueError):
            export_events([event], "unknown")
        with self.assertRaises(ValueError):
            export_events([event], "json", max_entries=0)
        with self.assertRaises(ValueError):
            export_events([event], "json", max_bytes=1)

    def test_api_retention_helpers(self):
        self.repository.append(self.event(self.now - timedelta(days=400), "old"))
        policy = RetentionPolicy(enabled=True, older_than_days=30, max_entries=10)
        preview = preview_retention(self.context, policy, self.now)
        self.assertEqual(len(preview.event_ids), 1)
        result = execute_retention(
            self.context, preview, "retention policy cleanup", "manager"
        )
        self.assertEqual(result.deleted, 1)
        self.assertEqual(self.repository.events(), [])

    def test_retention_governance_failure_is_explicit(self):
        self.repository.append(self.event(self.now - timedelta(days=400), "old"))
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=30), self.now
        )
        with patch.object(
            self.repository,
            "delete_and_journal",
            side_effect=RuntimeError("journal unavailable"),
        ):
            with self.assertRaisesRegex(RetentionExecutionError, "journal unavailable"):
                RetentionService(self.context, self.repository).execute(
                    preview, "retention policy cleanup", "manager"
                )
        self.assertEqual(len(self.repository.events()), 1)
        self.assertEqual(self.repository.journal(), [])
        self.assertEqual(self.repository.get_preview(preview.operation_id), preview)

    def test_export_request_validates_its_limits(self):
        with self.assertRaisesRegex(ValueError, "unsupported export format"):
            ExportRequest(format="pdf")
        with self.assertRaisesRegex(ValueError, "max_entries"):
            ExportRequest(format="json", max_entries=0)
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            ExportRequest(format="json", max_bytes=0)


def test_suite():
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(GovernanceTests))
    return suite
