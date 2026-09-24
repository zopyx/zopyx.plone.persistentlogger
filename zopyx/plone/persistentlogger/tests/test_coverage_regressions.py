"""Focused regression coverage for hardened validation and backend paths."""

from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from sqlmodel import Session, SQLModel, create_engine

from .. import file_logger
from ..browser.retention import (
    Export,
    Retention,
    RetentionGUI,
    _error_response,
    _integer,
)
from ..logger import PersistentLoggerAdapter
from ..models import DeletionPreview, LogEvent, RetentionPolicy
from ..serialization import sanitized_details
from ..storage.base import (
    BaseLogStorage,
    StorageConfigurationError,
    StorageIntegrityError,
    _verify_chain,
    event_digest,
    new_event_entry,
    new_governance_entry,
    selection_digest,
)
from ..storage.query import SortSpec
from ..storage.rdbms import (
    PreviewRecord,
    SQLRepository,
    get_engine,
    order_by_spec,
)
from ..storage.zodb import LOG_KEY, PREVIEW_KEY, AnnotationRepository
from .postgres import database_url, stop_container
from .test_governance_edges import (
    BaseStorageEdgeTests,
    BrowserAPIEdgeTests,
    GovernanceEdgeTests,
    IdentityEdgeTests,
    MigrationEdgeTests,
    SerializationEdgeTests,
)
from .test_storage_base import StubStorage
from .test_storage_rdbms import Context as RdbmsContext
from .test_storage_zodb import AnnotationStore
from .test_storage_zodb import Context as ZodbContext


class Request:
    def __init__(self, form=None, method="GET", response=True):
        self.form = dict(form or {})
        self.method = method
        self.response = Response() if response else None

    def get(self, name, default=None):
        return self.form.get(name, default)


class Response:
    def __init__(self):
        self.status = None
        self.headers = {}

    def setStatus(self, status, reason=None):
        self.status = status

    def setHeader(self, name, value):
        self.headers[name] = value


class BrowserAndSerializationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(__name__="coverage")

    def test_browser_validation_and_export_failure_paths(self):
        with self.assertRaisesRegex(ValueError, "required"):
            _integer(None, "limit")
        with self.assertRaisesRegex(ValueError, "required"):
            _integer("", "limit")
        no_response = SimpleNamespace()
        self.assertIn("invalid", _error_response(no_response, 400, "invalid", "bad"))

        request = Request({"max_entries": "101"}, method="POST")
        configured = SimpleNamespace(enabled=True, older_than_days=30, max_entries=10)
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=SimpleNamespace(policy=lambda: configured),
            ),
        ):
            payload = Retention(self.context, request).preview()
        self.assertEqual(request.response.status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_policy")

        operation_id = uuid4()
        request = Request(
            {"operation_id": str(operation_id), "reason": "short"}, method="POST"
        )
        repository = MagicMock()
        repository.get_preview.return_value = object()
        service = MagicMock()
        service.execute.side_effect = ValueError("reason is invalid")
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.RetentionService",
                return_value=service,
            ),
            patch(
                "plone.api.user.get_current",
                return_value=SimpleNamespace(getUserName=lambda: "manager"),
            ),
        ):
            payload = Retention(self.context, request).delete()
        self.assertEqual(request.response.status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_reason")

        request = Request({"format": "json"})
        repository = MagicMock()
        repository.search.return_value = SimpleNamespace(rows=(), total=100_001)
        with patch(
            "zopyx.plone.persistentlogger.browser.retention.get_repository",
            return_value=repository,
        ):
            payload = Export(self.context, request)()
        self.assertEqual(request.response.status, 413)
        self.assertEqual(json.loads(payload)["error"]["code"], "export_limit")

        request = Request({"format": "json"})
        repository.search.return_value = SimpleNamespace(rows=(), total=0)
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.export_events",
                side_effect=ValueError("export failed"),
            ),
        ):
            payload = Export(self.context, request)()
        self.assertEqual(request.response.status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_export")

    def test_retention_gui_skips_missing_preview_events_and_unknown_actions(self):
        event_id = uuid4()
        preview = DeletionPreview(
            uuid4(), "coverage", datetime(2026, 1, 1, tzinfo=UTC), (event_id,), "digest"
        )
        repository = MagicMock()
        repository.get_preview.return_value = preview
        repository.get.side_effect = [None, {"event_id": str(event_id)}]
        request = Request({"operation_id": str(preview.operation_id)})
        with patch(
            "zopyx.plone.persistentlogger.browser.retention.get_repository",
            return_value=repository,
        ):
            view = RetentionGUI(self.context, request)
            self.assertEqual(view.preview_events, [])
            repository.get.side_effect = [{"event_id": str(event_id)}]
            self.assertEqual(view.preview_events, [{"event_id": str(event_id)}])

        request = Request({"action": "unknown"}, method="POST")
        with (
            patch("zopyx.plone.persistentlogger.browser.retention.CheckAuthenticator"),
            patch(
                "zopyx.plone.persistentlogger.browser.retention.get_repository",
                return_value=repository,
            ),
        ):
            view = RetentionGUI(self.context, request)
            view.template = MagicMock(return_value="rendered")
            self.assertEqual(view(), "rendered")
        self.assertEqual(view.messages, [])

    def test_logger_redacts_all_secret_match_shapes_and_validates_paths(self):
        record = {
            "message": (
                'password="quoted" authorization="Bearer quoted-token" '
                "token=plain-token authorization=Basic plain-basic"
            )
        }
        self.assertTrue(file_logger._redact_secrets(record))
        self.assertNotIn("quoted-token", record["message"])
        self.assertNotIn("plain-token", record["message"])
        self.assertIn("Bearer [REDACTED]", record["message"])
        self.assertIn("Basic [REDACTED]", record["message"])
        untouched = {"message": 42}
        self.assertTrue(file_logger._redact_secrets(untouched))
        self.assertEqual(untouched["message"], 42)

        with self.assertRaisesRegex(ValueError, "safe basename"):
            file_logger._safe_log_path(Path("."), "../escape", ".log")
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as outside,
        ):
            root_path = Path(root)
            (root_path / "link.log").symlink_to(Path(outside) / "target.log")
            with self.assertRaisesRegex(ValueError, "confined"):
                file_logger._safe_log_path(root_path, "link", ".log")

        logger = MagicMock()
        with patch.object(file_logger, "new_logger", return_value=logger):
            self.assertIs(
                file_logger.get_logger(
                    prefix="text-only", log_stdout=False, log_as_json=False
                ),
                logger,
            )
        self.assertEqual(logger.add.call_count, 1)

    def test_legacy_adapter_filters_entries_and_initializes_annotations(self):
        first = {"date": datetime(2026, 1, 1, tzinfo=UTC), "uuid": "first"}
        second = {"date": datetime(2026, 1, 2, tzinfo=UTC), "uuid": "second"}
        repository = SimpleNamespace(events=lambda: [first, second])
        adapter = PersistentLoggerAdapter(self.context)
        with (
            patch(
                "zopyx.plone.persistentlogger.logger.get_repository",
                return_value=repository,
            ),
            patch("zopyx.plone.persistentlogger.logger.IAnnotations", return_value={}),
        ):
            self.assertEqual(
                PersistentLoggerAdapter.entries.fget(
                    adapter,
                    min_datetime=datetime(2026, 1, 2),
                    max_datetime=datetime(2026, 1, 2, 23, 59),
                ),
                [second],
            )
            self.assertIs(adapter.annotations, adapter.annotations)
        with self.assertRaises(ValueError):
            LogEvent(comment="bad severity", severity=123)

    def test_serialization_rejects_unrepresentable_details(self):
        from ..serialization import redact_sensitive

        self.assertEqual(redact_sensitive({"values": {"b", "a"}})["values"], ["a", "b"])
        with self.assertRaisesRegex(ValueError, "finite"):
            sanitized_details({"number": float("nan")})
        self.assertEqual(sanitized_details({"number": 1.5}), {"number": 1.5})
        with self.assertRaisesRegex(ValueError, "keys"):
            sanitized_details({1: "not a string key"})
        with patch(
            "zopyx.plone.persistentlogger.serialization.canonical_json",
            side_effect=ValueError("cannot encode"),
        ):
            with self.assertRaisesRegex(ValueError, "JSON-compatible"):
                sanitized_details({"safe": True})


class BaseStorageRegressionTests(unittest.TestCase):
    def test_base_validation_and_cleanup_branches(self):
        storage = StubStorage()
        self.assertEqual(storage._delete_events(()), (0, 0))
        self.assertEqual(StubStorage()._rebuild_event_chain(), "")
        self.assertTrue(_verify_chain([], lambda record: ""))
        self.assertFalse(_verify_chain(["not a record"], lambda record: ""))

        event = LogEvent(
            comment="duplicate", created_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        storage.append(event)
        with self.assertRaisesRegex(ValueError, "already exists"):
            storage.append(event)
        self.assertEqual(storage._rebuild_event_chain(), storage.last_digest())

        first = new_event_entry(
            LogEvent(comment="first", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        duplicate = dict(first)
        self.assertFalse(_verify_chain([first, duplicate], event_digest))
        self.assertFalse(
            _verify_chain([dict(first, integrity_digest="bad")], event_digest)
        )

        cycle = [
            {"uuid": "first", "previous_digest": "", "integrity_digest": "a"},
            {"uuid": "second", "previous_digest": "a", "integrity_digest": "b"},
            {"uuid": "third", "previous_digest": "b", "integrity_digest": "a"},
        ]
        self.assertFalse(
            _verify_chain(cycle, lambda record: record["integrity_digest"])
        )
        self.assertEqual(BaseLogStorage._chain_tail(cycle), "")

    def test_base_preview_consumption_and_stale_cleanup(self):
        class PreviewStorage(StubStorage):
            def __init__(self, preview):
                super().__init__()
                self.preview = preview

            def _load_preview(self, operation_id):
                return self.preview

        now = datetime(2026, 1, 1, tzinfo=UTC)
        event_id = uuid4()
        preview = DeletionPreview(
            uuid4(),
            "stub",
            now,
            (event_id,),
            selection_digest("stub", (event_id,), now),
        )
        storage = PreviewStorage(preview)
        self.assertEqual(storage._consume_preview(preview), preview)
        self.assertIsNone(
            storage._consume_preview(SimpleNamespace(operation_id=uuid4()))
        )
        with patch.object(storage, "_consume_preview", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing or stale"):
                storage.delete_preview(preview, "valid retention cleanup")

        event = LogEvent(comment="object digest", created_at=now)
        self.assertEqual(event_digest(event), event_digest(event))


class RdbmsPrimitiveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/coverage.db")
        SQLModel.metadata.create_all(self.engine)
        self.repository = SQLRepository(RdbmsContext("primitive"), engine=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_cached_engine_race_and_ascending_text_sort(self):
        sentinel = object()
        fake_engines = MagicMock()
        fake_engines.get.side_effect = [None, sentinel]
        with patch("zopyx.plone.persistentlogger.storage.rdbms._engines", fake_engines):
            self.assertIs(get_engine("race-url"), sentinel)
        self.assertEqual(len(order_by_spec(SortSpec("comment"))), 1)
        self.assertEqual(len(order_by_spec(SortSpec("created_at"))), 1)

    def test_direct_event_governance_head_and_delete_primitives(self):
        event = LogEvent(
            comment="primitive event", created_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        entry = new_event_entry(event)
        self.repository._store_event(entry)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.repository._store_event(entry)
        self.repository._store_event_head(entry)
        self.assertEqual(self.repository._load_event_head(), entry["integrity_digest"])

        governance = new_governance_entry("inspect", "manager", "primitive test")
        self.repository._store_governance(governance)
        self.repository._store_governance_head(governance)
        self.assertEqual(
            self.repository._load_governance_head(), governance["integrity_digest"]
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.repository._store_governance(governance)
        self.repository._store_governance_head(governance)

        deleted, missing = self.repository._delete_events(
            (UUID(entry["event_id"]), uuid4())
        )
        self.assertEqual((deleted, missing), (1, 1))
        self.assertEqual(self.repository._delete_events(()), (0, 0))

    def test_preview_consume_handles_missing_mismatch_and_success(self):
        event = LogEvent(
            comment="old primitive event",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        self.repository.append(event)
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=1),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertIsNone(
            self.repository._consume_preview(
                DeletionPreview(
                    uuid4(),
                    preview.object_uid,
                    preview.cutoff,
                    preview.event_ids,
                    preview.selection_digest,
                )
            )
        )
        self.assertIsNone(
            self.repository._consume_preview(
                DeletionPreview(
                    preview.operation_id,
                    preview.object_uid,
                    preview.cutoff,
                    preview.event_ids,
                    "wrong",
                )
            )
        )
        self.assertEqual(self.repository._consume_preview(preview), preview)

    def test_rdbms_validation_recovery_and_removal_paths(self):
        from ..storage.rdbms import ChainHeadRecord, _pool_value

        with patch.dict("os.environ", {"PERSISTENT_LOGGER_TEST_POOL": "bad"}):
            with self.assertRaisesRegex(
                StorageConfigurationError, "must be an integer"
            ):
                _pool_value("PERSISTENT_LOGGER_TEST_POOL", 1)
        with patch.dict("os.environ", {"PERSISTENT_LOGGER_TEST_POOL": "-1"}):
            with self.assertRaisesRegex(
                StorageConfigurationError, "must not be negative"
            ):
                _pool_value("PERSISTENT_LOGGER_TEST_POOL", 1)

        event = LogEvent(comment="head", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        self.repository.append(event)
        with Session(self.engine) as session, session.begin():
            head = session.get(ChainHeadRecord, self.repository.uid)
            head.event_digest = "wrong"
        with self.assertRaisesRegex(StorageIntegrityError, "does not match"):
            self.repository.append(
                LogEvent(comment="next", created_at=datetime(2026, 1, 2, tzinfo=UTC))
            )
        self.repository._reset_event_head()
        self.assertEqual(
            self.repository._load_event_head(),
            event_digest(self.repository.events()[0]),
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.repository._rewrite_event(
                {"event_id": str(uuid4()), "created_at": datetime.now(UTC)}
            )
        self.repository._remove_all_events()
        self.assertEqual(self.repository.events(), [])

        preview_record = PreviewRecord(
            operation_id=str(uuid4()),
            object_uid=self.repository.uid,
            cutoff=datetime(2026, 1, 1),
            event_ids=None,
            selection_digest="selection",
            expires_at=None,
        )
        preview = self.repository._preview_from_record(preview_record)
        self.assertEqual(preview.event_ids, ())
        self.assertIsNone(preview.expires_at)

        removable = SQLRepository(RdbmsContext("removable"), engine=self.engine)
        removable.append(LogEvent(comment="remove", created_at=datetime.now(UTC)))
        removable.record_governance("inspect", "actor", "remove rows")
        removable.set_policy(RetentionPolicy(enabled=True))
        removable.preview_delete(RetentionPolicy(), datetime.now(UTC))
        self.assertGreater(removable.remove_object(), 0)
        self.assertEqual(removable.events(), [])
        self.assertEqual(removable.journal(), [])

        missing_repo = SQLRepository(
            RdbmsContext("missing-preview"), engine=self.engine
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        missing_preview = missing_repo.preview_delete(RetentionPolicy(), now)
        self.assertEqual(
            missing_repo._consume_preview(missing_preview), missing_preview
        )
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            missing_repo.delete_and_journal(
                missing_preview, "valid journal reason", "actor", now
            )
        mismatch_preview = missing_repo.preview_delete(RetentionPolicy(), now)
        mismatch = DeletionPreview(
            mismatch_preview.operation_id,
            mismatch_preview.object_uid,
            mismatch_preview.cutoff,
            mismatch_preview.event_ids,
            "wrong",
            mismatch_preview.expires_at,
        )
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            missing_repo.delete_and_journal(
                mismatch, "valid journal reason", "actor", now
            )
        with self.assertRaisesRegex(ValueError, "at least 10"):
            missing_repo.delete_and_journal(mismatch_preview, "short", "actor")


class ZodbPrimitiveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.store = AnnotationStore()
        self.context = ZodbContext("zodb-primitive")
        self.patcher = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=self.store,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.repository = AnnotationRepository(self.context)

    def test_duplicate_lookup_and_legacy_delete_paths(self):
        event = LogEvent(
            comment="zodb event", created_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        entry = self.repository.append(event)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.repository._store_event(entry)
        self.store[LOG_KEY]["legacy-key"] = self.store[LOG_KEY].pop(entry["uuid"])
        deleted, missing = self.repository._delete_events(
            (UUID(entry["uuid"]), uuid4())
        )
        self.assertEqual((deleted, missing), (1, 1))
        self.assertIsNone(self.repository._load_event(["not", "hashable"]))
        broken_store = MagicMock()
        broken_store.get.side_effect = TypeError("unusable key")
        with (
            patch(
                "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
                return_value={LOG_KEY: broken_store},
            ),
            patch("zopyx.plone.persistentlogger.storage.zodb.migrate_store"),
        ):
            self.assertIsNone(self.repository._load_event("broken"))

        legacy_id = uuid4()
        with patch("zopyx.plone.persistentlogger.storage.zodb.migrate_store"):
            store = self.repository.annotations
            store["legacy-only"] = {
                "uuid": str(legacy_id),
                "date": datetime(2020, 1, 1, tzinfo=UTC),
            }
            deleted, missing = self.repository._delete_events((legacy_id,))
        self.assertEqual((deleted, missing), (1, 0))

    def test_preview_consume_handles_missing_and_mismatch(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        preview = DeletionPreview(
            uuid4(), self.repository.object_uid(), now, (), "digest"
        )
        self.assertIsNone(self.repository._consume_preview(preview))
        self.repository._store_preview(preview)
        self.assertIsNone(
            self.repository._consume_preview(
                DeletionPreview(
                    preview.operation_id,
                    preview.object_uid,
                    preview.cutoff,
                    (),
                    "wrong",
                )
            )
        )
        self.assertEqual(self.repository._consume_preview(preview), preview)
        self.assertIn(PREVIEW_KEY, self.store)

    def test_zodb_transaction_rollback_and_cleanup_paths(self):
        from persistent.mapping import PersistentMapping

        from ..storage.zodb import CHAIN_HEAD_KEY

        now = datetime(2026, 1, 1, tzinfo=UTC)
        preview = DeletionPreview(
            uuid4(), self.repository.object_uid(), now, (), "selection"
        )
        savepoint = MagicMock()
        with (
            patch(
                "zopyx.plone.persistentlogger.storage.zodb.transaction.savepoint",
                return_value=savepoint,
            ),
            patch(
                "zopyx.plone.persistentlogger.storage.zodb.BaseLogStorage.delete_and_journal",
                side_effect=RuntimeError("journal"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "journal"):
                self.repository.delete_and_journal(
                    preview, "valid journal reason", "actor"
                )
        savepoint.rollback.assert_called_once_with()

        event_id = str(uuid4())
        self.store[LOG_KEY] = AnnotationStore(
            {"legacy": {"event_id": event_id, "uuid": event_id}}
        )
        self.store[CHAIN_HEAD_KEY] = {"event_digest": "digest"}
        self.repository._remove_all_events()
        self.assertEqual(self.store[LOG_KEY], {})
        self.assertNotIn(CHAIN_HEAD_KEY, self.store)

        expired = DeletionPreview(
            uuid4(), self.repository.object_uid(), now, (), "selection", expires_at=now
        )
        previews = PersistentMapping({"expired": expired})
        self.store[PREVIEW_KEY] = previews
        self.assertEqual(self.repository.cleanup_expired_previews(now), 1)
        self.assertEqual(previews, {})


class PostgresHelperRegressionTests(unittest.TestCase):
    def setUp(self):
        import zopyx.plone.persistentlogger.tests.postgres as postgres

        self.postgres = postgres
        self.saved = (postgres._container, postgres._url, postgres._skip_reason)
        postgres._container = None
        postgres._url = None
        postgres._skip_reason = None
        self.addCleanup(self.restore)

    def restore(self):
        self.postgres._container, self.postgres._url, self.postgres._skip_reason = (
            self.saved
        )

    def test_stop_failure_is_swallowed_after_start_failure(self):
        container = MagicMock()
        container.start.side_effect = RuntimeError("start failed")
        container.stop.side_effect = RuntimeError("stop failed")
        with (
            patch.dict("os.environ", {self.postgres.REQUIRE_VARIABLE: ""}),
            patch.object(self.postgres, "_postgres_container", return_value=container),
        ):
            with self.assertRaises(unittest.SkipTest):
                database_url()
        container.stop.assert_called_once_with()

    def test_stop_container_without_a_container_only_disposes_engines(self):
        self.postgres._url = "cached"
        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.dispose_engines"
        ) as dispose:
            stop_container()
        dispose.assert_called_once_with()
        self.assertIsNone(self.postgres._url)

    def test_security_hardening_module_main_guard_is_safe(self):
        with patch("unittest.main") as main:
            runpy.run_module(
                "zopyx.plone.persistentlogger.tests.test_security_hardening",
                run_name="__main__",
            )
        main.assert_called_once_with()


def test_suite():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for test_case in (
        BrowserAndSerializationRegressionTests,
        BaseStorageRegressionTests,
        RdbmsPrimitiveRegressionTests,
        ZodbPrimitiveRegressionTests,
        PostgresHelperRegressionTests,
        GovernanceEdgeTests,
        IdentityEdgeTests,
        MigrationEdgeTests,
        BrowserAPIEdgeTests,
        SerializationEdgeTests,
        BaseStorageEdgeTests,
    ):
        suite.addTest(loader.loadTestsFromTestCase(test_case))
    return suite
