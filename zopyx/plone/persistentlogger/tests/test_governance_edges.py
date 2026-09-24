"""Edge coverage for bounded governance and integrity helpers."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ..data_subject import (
    HOLD_KEY,
    DataSubjectQuery,
    LegalHold,
    QuotaExceeded,
    create_hold,
    export_journal,
    holds,
    is_held,
    release_hold,
    search_data_subject,
)
from ..integrity import verify_repository


class GovernanceEdgeTests(unittest.TestCase):
    def context(self, uid="object-1"):
        return SimpleNamespace(__uid__=uid)

    def repository(self, events=(), journal=()):
        repo = MagicMock()
        repo.events.return_value = list(events)
        repo.journal.return_value = list(journal)
        repo.record_governance.return_value = {}
        return repo

    def test_query_selectors_and_limits(self):
        entry = {"event_id": "event-1", "actor": "Alice", "target": "Subject"}
        self.assertTrue(DataSubjectQuery(actor="alice").matches(entry))
        self.assertFalse(DataSubjectQuery(actor="bob").matches(entry))
        self.assertTrue(DataSubjectQuery(target="subject").matches(entry))
        self.assertFalse(DataSubjectQuery(target="other").matches(entry))
        self.assertTrue(DataSubjectQuery(event_id="EVENT-1").matches(entry))
        self.assertFalse(DataSubjectQuery(event_id="event-2").matches(entry))
        context = self.context()
        with patch(
            "zopyx.plone.persistentlogger.data_subject.get_repository",
            return_value=self.repository(),
        ):
            for kwargs in (
                {"limit": 0},
                {"limit": 1001},
                {"site_limit": 0},
                {"site_limit": 10001},
            ):
                with self.assertRaises(ValueError):
                    search_data_subject([context], DataSubjectQuery(), **kwargs)
            with self.assertRaises(ValueError):
                export_journal([context], limit=0)

    def test_search_deduplicates_objects_and_marks_holds(self):
        context = self.context()
        event = {
            "event_id": "event-1",
            "actor": "Alice",
            "created_at": datetime.now(UTC),
        }
        repo = self.repository([event])
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=repo,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="object-1",
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.is_held", return_value=True
            ),
        ):
            result = search_data_subject([context, context], DataSubjectQuery())
        self.assertEqual((result.total, result.objects_scanned), (1, 1))
        self.assertTrue(result.rows[0]["legal_hold"])
        self.assertEqual(result.rows[0]["object_uid"], "object-1")

    def test_search_and_export_quotas(self):
        context = self.context()
        event = {"event_id": "event-1", "actor": "Alice"}
        repo = self.repository(
            [event, dict(event, event_id="event-2")],
            [{"event_id": "j1"}, {"event_id": "j2"}],
        )
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=repo,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="object-1",
            ),
        ):
            with self.assertRaises(QuotaExceeded):
                search_data_subject([context], DataSubjectQuery(), site_limit=1)
            with self.assertRaises(QuotaExceeded):
                export_journal([context], limit=1)
            payload = json.loads(export_journal([context], limit=2))
        self.assertEqual(len(payload["records"]), 2)

    def test_hold_store_parsing_and_release_errors(self):
        context = self.context()
        annotations = {
            HOLD_KEY: {
                "bad": object(),
                "released": {
                    "hold_id": "released",
                    "object_uid": "object-1",
                    "actor": "a",
                    "reason": "long enough reason",
                    "created_at": datetime.now(UTC).isoformat(),
                    "released_at": datetime.now(UTC).isoformat(),
                    "event_ids": [],
                },
            }
        }
        with patch(
            "zopyx.plone.persistentlogger.data_subject.IAnnotations",
            return_value=annotations,
        ):
            self.assertEqual(holds(context), ())
            self.assertEqual(len(holds(context, active_only=False)), 1)
            self.assertFalse(is_held(context, "event-1"))
        with patch(
            "zopyx.plone.persistentlogger.data_subject.IAnnotations",
            side_effect=TypeError,
        ):
            self.assertEqual(holds(context), ())
            with self.assertRaises(ValueError):
                create_hold(context, "actor", "a sufficiently long reason")

    def test_hold_create_release_rollback_and_validation(self):
        context = self.context()
        annotations = {}
        repo = self.repository()
        repo.retention_lock.return_value.__enter__.return_value = None
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.IAnnotations",
                return_value=annotations,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=repo,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="object-1",
            ),
        ):
            with self.assertRaises(ValueError):
                create_hold(context, "actor", "short")
            hold = create_hold(
                context,
                "actor",
                "a sufficiently long reason",
                event_ids=["b", "a", "a"],
            )
            self.assertEqual(hold.event_ids, ("a", "b"))
            with self.assertRaises(ValueError):
                release_hold(context, "missing", "actor", "a sufficiently long reason")
            released = release_hold(
                context, hold.hold_id, "actor", "a sufficiently long reason"
            )
            self.assertFalse(released.active)
            with self.assertRaises(ValueError):
                release_hold(
                    context, hold.hold_id, "actor", "a sufficiently long reason"
                )

    def test_hold_journal_failures_roll_back(self):
        context = self.context()
        annotations = {}
        repo = self.repository()
        repo.record_governance.side_effect = RuntimeError("backend")
        with (
            patch(
                "zopyx.plone.persistentlogger.data_subject.IAnnotations",
                return_value=annotations,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.get_repository",
                return_value=repo,
            ),
            patch(
                "zopyx.plone.persistentlogger.data_subject.object_uid",
                return_value="object-1",
            ),
        ):
            with self.assertRaises(RuntimeError):
                create_hold(context, "actor", "a sufficiently long reason")
        self.assertEqual(annotations[HOLD_KEY], {})

    def test_integrity_report_and_sequence_states(self):
        repo = self.repository()
        repo.object_uid.return_value = "object-1"
        with (
            patch(
                "zopyx.plone.persistentlogger.integrity.verify_event_chain",
                return_value=True,
            ),
            patch(
                "zopyx.plone.persistentlogger.integrity.verify_governance_chain",
                return_value=True,
            ),
        ):
            report = verify_repository(repo)
        self.assertTrue(report.ok)
        self.assertEqual(report.sequence_status, "not_available")
        self.assertEqual(report.as_dict()["status"], "healthy")

        for rows, status in (
            ([{"event_id": "1", "sequence": "bad"}], "invalid"),
            (
                [{"event_id": "1", "sequence": 2}, {"event_id": "2", "sequence": 1}],
                "unverifiable",
            ),
        ):
            repo.events.return_value = rows
            with (
                patch(
                    "zopyx.plone.persistentlogger.integrity.verify_event_chain",
                    return_value=True,
                ),
                patch(
                    "zopyx.plone.persistentlogger.integrity.verify_governance_chain",
                    return_value=True,
                ),
            ):
                self.assertEqual(verify_repository(repo).sequence_status, status)

    def test_integrity_report_non_monotone_and_schema_warning(self):
        repo = self.repository(
            [
                {
                    "event_id": "1",
                    "previous_digest": "",
                    "integrity_digest": "a",
                    "sequence": 1,
                    "schema_version": 9,
                },
                {
                    "event_id": "2",
                    "previous_digest": "a",
                    "integrity_digest": "b",
                    "sequence": 1,
                    "schema_version": 9,
                },
            ]
        )
        repo.object_uid.return_value = "object-1"
        with (
            patch(
                "zopyx.plone.persistentlogger.integrity.verify_event_chain",
                return_value=True,
            ),
            patch(
                "zopyx.plone.persistentlogger.integrity.verify_governance_chain",
                return_value=True,
            ),
        ):
            report = verify_repository(repo)
        self.assertEqual(report.sequence_status, "non_monotone")
        self.assertFalse(report.schema_ok)
        self.assertEqual(report.status, "unhealthy")


class IdentityEdgeTests(unittest.TestCase):
    def test_stable_site_key_fallbacks(self):
        from ..site_identity import stable_site_key

        self.assertEqual(stable_site_key(None), ("none",))
        self.assertEqual(
            stable_site_key(SimpleNamespace(getPhysicalPath=lambda: ("a", "b"))),
            ("path", "a", "b"),
        )
        named = SimpleNamespace(__name__="site")
        with patch("plone.uuid.interfaces.IUUID", return_value="uuid-1"):
            self.assertEqual(stable_site_key(named), ("uuid", "uuid-1"))
        self.assertEqual(
            stable_site_key(SimpleNamespace(__name__="site")), ("name", "site")
        )
        self.assertEqual(stable_site_key(object())[0], "class")


class MigrationEdgeTests(unittest.TestCase):
    def test_migrate_store_quarantines_invalid_and_duplicates(self):
        from ..migrations.v1 import migrate_store

        first = {"uuid": "same", "date": datetime(2024, 1, 1), "sequence": "bad"}
        duplicate = {"uuid": "same", "date": datetime(2024, 1, 2)}
        store = {"bad": object(), "first": first, "duplicate": duplicate}
        quarantine = {}
        changed = migrate_store(store, quarantine)
        self.assertEqual(changed, 3)
        self.assertEqual(len(quarantine), 2)
        self.assertEqual(len(store), 1)

    def test_migrate_store_preserves_invalid_without_quarantine(self):
        from ..migrations.v1 import migrate_store

        value = {"uuid": "bad", "sequence": 0}
        store = {"bad": value, "scalar": "legacy"}
        self.assertEqual(migrate_store(store), 0)
        self.assertEqual(store["scalar"], "legacy")

    def test_migrate_annotations_and_site_walk_are_bounded(self):
        from ..migrations.v1 import LOG_KEY, migrate_annotations, migrate_site

        annotations = {LOG_KEY: {"event": {"uuid": "e1", "date": datetime.now(UTC)}}}
        self.assertEqual(migrate_annotations(annotations), 1)
        self.assertIn(LOG_KEY, annotations)
        self.assertEqual(migrate_annotations({}), 0)
        obj = SimpleNamespace()
        site = SimpleNamespace(portal_catalog=None, getSite=lambda: site)
        with patch(
            "zopyx.plone.persistentlogger.migrations.v1.IAnnotations", return_value={}
        ):
            self.assertEqual(migrate_site(site), 0)
        self.assertEqual(migrate_site(obj), 0)


class BrowserAPIEdgeTests(unittest.TestCase):
    def context(self, allowed=True):
        return SimpleNamespace(
            portal_membership=SimpleNamespace(checkPermission=lambda *_: allowed)
        )

    def test_audit_api_success_and_recording_failures(self):
        from ..browser.api import AuditAPI
        from ..data_subject import DataSubjectSearchResult

        context = self.context()
        request = __import__(
            "zopyx.plone.persistentlogger.tests.test_m5_m8", fromlist=["Request"]
        ).Request()
        repo = MagicMock()
        repo.record_governance.side_effect = RuntimeError("backend")
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.api.search_data_subject",
                return_value=DataSubjectSearchResult(({},), 1, 1),
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.api.get_repository",
                return_value=repo,
            ),
        ):
            payload = json.loads(AuditAPI(context, request)())
        self.assertEqual(request.status, 500)
        self.assertEqual(payload["error"]["code"], "internal_error")

        request = __import__(
            "zopyx.plone.persistentlogger.tests.test_m5_m8", fromlist=["Request"]
        ).Request(method="POST")
        payload = json.loads(AuditAPI(context, request)())
        self.assertEqual(request.status, 405)
        self.assertEqual(payload["error"]["code"], "method_not_allowed")

    def test_export_and_hold_api_error_and_success_paths(self):
        from ..browser.api import AuditExportAPI, HoldAPI
        from ..tests.test_m5_m8 import Request

        context = self.context()
        request = Request(method="POST")
        payload = json.loads(AuditExportAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "method_not_allowed")
        request = Request()
        with patch(
            "zopyx.plone.persistentlogger.browser.api.export_journal",
            side_effect=QuotaExceeded("quota"),
        ):
            payload = json.loads(AuditExportAPI(context, request)())
        self.assertEqual(request.status, 413)
        self.assertEqual(payload["error"]["code"], "quota_exceeded")

        request = Request({"action": "unknown"}, method="POST")
        with patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"):
            payload = json.loads(HoldAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "invalid_action")
        request = Request(
            {"action": "create", "reason": "valid hold reason"}, method="POST"
        )
        hold = LegalHold(
            "h", "object-1", "actor", "valid hold reason", datetime.now(UTC)
        )
        with (
            patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.api.create_hold",
                return_value=hold,
            ),
        ):
            payload = json.loads(HoldAPI(context, request)())
        self.assertEqual(payload["hold_id"], "h")

    def test_api_validation_helpers_and_remaining_paths(self):
        from ..browser import api
        from ..browser.api import AuditAPI, AuditExportAPI, HoldAPI
        from ..tests.test_m5_m8 import Request

        for params in ({"event_id": "bad id"}, {"limit": "nope"}, {"hold_id": "bad"}):
            with self.assertRaises(ValueError):
                if "event_id" in params:
                    api._query(Request(params))
                elif "limit" in params:
                    api._limit(Request(params), 10)
                else:
                    api._hold_id(Request(params))
        context = self.context()
        request = Request({"offset": True})
        payload = json.loads(AuditAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "invalid_query")
        with patch(
            "zopyx.plone.persistentlogger.browser.api.search_data_subject",
            side_effect=QuotaExceeded("quota"),
        ):
            request = Request()
            payload = json.loads(AuditAPI(context, request)())
        self.assertEqual(request.status, 413)
        request = Request()
        with patch(
            "zopyx.plone.persistentlogger.browser.api.export_journal",
            side_effect=ValueError("invalid"),
        ):
            payload = json.loads(AuditExportAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "invalid_query")
        denied = self.context(False)
        request = Request()
        payload = json.loads(AuditExportAPI(denied, request)())
        self.assertEqual(payload["error"]["code"], "forbidden")

        request = Request({"action": "create", "reason": "x" * 3000}, method="POST")
        with patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"):
            payload = json.loads(HoldAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "invalid_hold")
        request = Request(
            {"action": "create", "reason": "valid reason", "event_ids": "bad id"},
            method="POST",
        )
        with patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"):
            payload = json.loads(HoldAPI(context, request)())
        self.assertEqual(payload["error"]["code"], "invalid_hold")

        from ..browser.api import HoldAPI
        from ..tests.test_m5_m8 import Request

        context = self.context()
        request = Request(method="POST")
        with patch(
            "zopyx.plone.persistentlogger.browser.api.CheckAuthenticator",
            side_effect=RuntimeError("csrf"),
        ):
            with self.assertRaises(RuntimeError):
                HoldAPI(context, request)()

    def test_api_success_and_validation_exception_paths(self):
        from ..browser import api
        from ..browser.api import AuditAPI, AuditExportAPI, HoldAPI
        from ..data_subject import DataSubjectSearchResult
        from ..tests.test_m5_m8 import Request

        valid = Request({"event_id": "event-1"})
        self.assertEqual(api._query(valid).event_id, "event-1")
        with self.assertRaises(ValueError):
            api._query(Request({"actor": "x" * 2049}))
        with self.assertRaises(ValueError):
            api._hold_id(Request({"hold_id": 123}))

        failing_context = SimpleNamespace(
            portal_membership=SimpleNamespace(
                checkPermission=lambda *_: (_ for _ in ()).throw(RuntimeError("lookup"))
            )
        )
        request = Request()
        payload = json.loads(AuditExportAPI(failing_context, request)())
        self.assertEqual((request.status, payload["error"]["code"]), (403, "forbidden"))

        repository = MagicMock()
        result = DataSubjectSearchResult(({"event_id": "event-1"},), 1, 1)
        request = Request({"limit": "1"})
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.api.search_data_subject",
                return_value=result,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.api.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.api.object_uid",
                return_value="object-1",
            ),
        ):
            payload = json.loads(AuditAPI(self.context(), request)())
        self.assertEqual(payload["records"], [{"event_id": "event-1"}])
        self.assertEqual(request.headers["Content-Type"], "application/json")

        request = Request()
        with patch(
            "zopyx.plone.persistentlogger.browser.api.export_journal",
            side_effect=RuntimeError("backend"),
        ):
            payload = json.loads(AuditExportAPI(self.context(), request)())
        self.assertEqual(
            (request.status, payload["error"]["code"]), (500, "internal_error")
        )

        request = Request()
        payload = json.loads(HoldAPI(self.context(), request)())
        self.assertEqual(
            (request.status, payload["error"]["code"]), (405, "method_not_allowed")
        )

        with patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"):
            request = Request(
                {"action": "create", "event_ids": "a" * (1000 * 256 + 1)},
                method="POST",
            )
            payload = json.loads(HoldAPI(self.context(), request)())
            self.assertEqual(payload["error"]["code"], "invalid_hold")
            request = Request(
                {"action": "release", "reason": "x" * 2049}, method="POST"
            )
            payload = json.loads(HoldAPI(self.context(), request)())
            self.assertEqual(payload["error"]["code"], "invalid_hold")

        hold_id = str(uuid4())
        hold = LegalHold(
            hold_id, "object-1", "actor", "release reason", datetime.now(UTC)
        )
        with (
            patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.api.release_hold",
                return_value=hold,
            ),
        ):
            request = Request(
                {
                    "action": "release",
                    "hold_id": str(hold.hold_id),
                    "reason": "release reason",
                },
                method="POST",
            )
            payload = json.loads(HoldAPI(self.context(), request)())
        self.assertEqual(payload["hold_id"], hold_id)

        with (
            patch("zopyx.plone.persistentlogger.browser.api.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.api.create_hold",
                side_effect=RuntimeError("backend"),
            ),
        ):
            request = Request(
                {"action": "create", "reason": "valid reason"}, method="POST"
            )
            payload = json.loads(HoldAPI(self.context(), request)())
        self.assertEqual(
            (request.status, payload["error"]["code"]), (500, "internal_error")
        )

    def test_integrity_health_success_response(self):
        from ..browser.integrity import IntegrityHealthView
        from ..tests.test_m5_m8 import Request

        report = MagicMock()
        report.as_dict.return_value = {"status": "healthy"}
        request = Request()
        with (
            patch("zopyx.plone.persistentlogger.browser.integrity.get_repository"),
            patch(
                "zopyx.plone.persistentlogger.browser.integrity.verify_repository",
                return_value=report,
            ),
        ):
            payload = json.loads(IntegrityHealthView(self.context(), request)())
        self.assertEqual(payload, {"status": "healthy"})
        self.assertEqual(request.headers["Cache-Control"], "no-store")

        from ..migrations.v1 import (
            LOG_KEY,
            QUARANTINE_KEY,
            _site_objects,
            migrate_annotations,
            migrate_site,
            migrate_store,
            upgrade_to_3,
        )

        class FailingQuarantine(dict):
            failed = False

            def __setitem__(self, key, value):
                if not self.failed:
                    self.failed = True
                    raise TypeError("legacy value")
                return super().__setitem__(key, value)

        quarantine = FailingQuarantine()
        self.assertEqual(migrate_store({"bad": object()}, quarantine), 1)
        self.assertIn("record", next(iter(quarantine.values())))
        invalid_schema = {"bad": {"uuid": "schema", "schema_version": "bad"}}
        self.assertEqual(migrate_store(invalid_schema), 1)
        invalid_sequence = {"bad": {"uuid": "sequence", "sequence": 0}}
        self.assertEqual(migrate_store(invalid_sequence), 0)
        self.assertEqual(invalid_sequence["bad"]["sequence"], 0)
        valid_sequence = {"good": {"uuid": "sequence", "sequence": "2"}}
        self.assertEqual(migrate_store(valid_sequence), 1)
        self.assertEqual(next(iter(valid_sequence.values()))["sequence"], 2)

        duplicate = {
            "first": {"uuid": "same", "date": datetime(2024, 1, 1)},
            "second": {"uuid": "same", "date": datetime(2024, 1, 2)},
        }
        duplicate_quarantine = {}
        self.assertEqual(migrate_store(duplicate, duplicate_quarantine), 2)
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(len(duplicate_quarantine), 1)

        annotations = {LOG_KEY: {"bad": object()}}
        self.assertEqual(migrate_annotations(annotations), 1)
        self.assertIn(QUARANTINE_KEY, annotations)

        site = SimpleNamespace(portal_catalog=None)
        with patch(
            "Products.CMFCore.utils.getToolByName", side_effect=RuntimeError("catalog")
        ):
            self.assertEqual(_site_objects(site), [site])
        failing_catalog = SimpleNamespace(
            unrestrictedSearchResults=lambda: (_ for _ in ()).throw(
                RuntimeError("search")
            )
        )
        failing_site = SimpleNamespace(portal_catalog=failing_catalog)
        self.assertEqual(_site_objects(failing_site), [failing_site])
        bad_brain = SimpleNamespace(
            _unrestrictedGetObject=lambda: (_ for _ in ()).throw(RuntimeError("object"))
        )
        catalog = SimpleNamespace(unrestrictedSearchResults=lambda: [bad_brain])
        bad_brain_site = SimpleNamespace(portal_catalog=catalog)
        self.assertEqual(_site_objects(bad_brain_site), [bad_brain_site])

        duplicate_site = SimpleNamespace()
        brain = SimpleNamespace(_unrestrictedGetObject=lambda: duplicate_site)
        duplicate_site.portal_catalog = SimpleNamespace(
            unrestrictedSearchResults=lambda: [brain]
        )
        with patch(
            "zopyx.plone.persistentlogger.migrations.v1.IAnnotations", return_value={}
        ):
            self.assertEqual(migrate_site(duplicate_site), 0)
        with patch(
            "zopyx.plone.persistentlogger.migrations.v1.migrate_site"
        ) as migrate:
            upgrade_to_3(duplicate_site)
        migrate.assert_called_once_with(duplicate_site)


class SerializationEdgeTests(unittest.TestCase):
    def test_serialization_date_and_schema_fallbacks(self):
        from ..serialization import (
            _canonical_datetime,
            canonical_event_date,
            canonical_event_payload,
        )

        self.assertEqual(
            _canonical_datetime(datetime(2026, 1, 2, tzinfo=UTC)).tzinfo, UTC
        )
        self.assertEqual(_canonical_datetime(datetime(2026, 1, 2).date()).day, 2)
        self.assertEqual(_canonical_datetime("2026-01-02T03:04:05Z").year, 2026)
        self.assertEqual(
            _canonical_datetime("not-a-date"), datetime.min.replace(tzinfo=UTC)
        )
        self.assertEqual(
            _canonical_datetime(object()), datetime.min.replace(tzinfo=UTC)
        )
        self.assertEqual(
            canonical_event_date(SimpleNamespace(created_at="invalid")),
            datetime.min.replace(tzinfo=UTC),
        )
        payload = canonical_event_payload({"event_id": "e", "schema_version": "bad"})
        self.assertEqual(payload["schema_version"], 1)


class BaseStorageEdgeTests(unittest.TestCase):
    def test_base_integrity_and_lifecycle_guards(self):
        from ..models import DeletionPreview, LogEvent
        from ..storage.base import (
            BaseLogStorage,
            StorageIntegrityError,
            _verify_chain,
            new_event_entry,
            selection_digest,
        )
        from .test_storage_base import StubStorage

        with self.assertRaisesRegex(NotImplementedError, ""):
            StubStorage()._remove_all_events()
        self.assertEqual(BaseLogStorage._chain_tail([]), "")
        self.assertEqual(
            BaseLogStorage._chain_tail(
                [{"uuid": "x", "previous_digest": "missing", "integrity_digest": "a"}]
            ),
            "",
        )
        duplicate_previous = [
            {"uuid": "a", "previous_digest": "", "integrity_digest": "a"},
            {"uuid": "b", "previous_digest": "", "integrity_digest": "b"},
        ]
        self.assertEqual(len(BaseLogStorage._chain_order(duplicate_previous)), 2)
        disconnected = [
            {"uuid": "a", "previous_digest": "", "integrity_digest": "a"},
            {"uuid": "b", "previous_digest": "missing", "integrity_digest": "b"},
        ]
        self.assertEqual(len(BaseLogStorage._chain_order(disconnected)), 2)
        self.assertFalse(
            _verify_chain([{"uuid": "x"}], lambda _: (_ for _ in ()).throw(TypeError()))
        )
        self.assertFalse(
            _verify_chain(
                [{"uuid": "x", "integrity_digest": "a"}], lambda _: "a", "other"
            )
        )

        class HeadStorage(StubStorage):
            def __init__(self, entries=(), head=""):
                super().__init__(entries)
                self.head = head

            def _load_event_head(self):
                return self.head

        empty = HeadStorage(head="orphan")
        with self.assertRaisesRegex(StorageIntegrityError, "no event record"):
            empty.append(LogEvent(comment="orphan", created_at=datetime.now(UTC)))
        event = LogEvent(comment="head mismatch", created_at=datetime.now(UTC))
        with self.assertRaisesRegex(ValueError, "positive"):
            new_event_entry(event, sequence=0)
        entry = new_event_entry(event)
        mismatch = HeadStorage([entry], head="wrong")
        with self.assertRaisesRegex(StorageIntegrityError, "does not match"):
            mismatch.append(LogEvent(comment="next", created_at=datetime.now(UTC)))
        invalid = StubStorage([dict(entry, integrity_digest="bad")])
        self.assertEqual(invalid.last_digest(), "")
        self.assertEqual(invalid.cleanup_expired_previews(), 0)
        self.assertEqual(invalid.remove_object(), 0)

        now = datetime(2026, 1, 1, tzinfo=UTC)
        event_id = uuid4()
        preview = DeletionPreview(
            uuid4(),
            "stub",
            now,
            (event_id,),
            selection_digest("stub", (event_id,), now),
            expires_at=now,
        )

        class PreviewStorage(StubStorage):
            def _load_preview(self, operation_id):
                return preview

        storage = PreviewStorage()
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            storage._validated_preview(preview, now + timedelta(seconds=1))
        broken = StubStorage(
            [{"uuid": "bad", "previous_digest": "x", "integrity_digest": "y"}]
        )
        broken_preview = DeletionPreview(
            uuid4(), "stub", now, (), selection_digest("stub", (), now)
        )
        broken._load_preview = lambda _: broken_preview
        with self.assertRaisesRegex(StorageIntegrityError, "unverifiable"):
            broken.delete_preview(broken_preview, "valid retention cleanup")
        with self.assertRaisesRegex(ValueError, "at least 10"):
            storage.delete_and_journal(preview, "short", "actor")


def test_suite():
    """Register edge coverage with the canonical Zope test runner."""
    loader = unittest.defaultTestLoader
    return unittest.TestSuite(
        loader.loadTestsFromTestCase(test_case)
        for test_case in (
            GovernanceEdgeTests,
            IdentityEdgeTests,
            MigrationEdgeTests,
            BrowserAPIEdgeTests,
            SerializationEdgeTests,
            BaseStorageEdgeTests,
        )
    )
