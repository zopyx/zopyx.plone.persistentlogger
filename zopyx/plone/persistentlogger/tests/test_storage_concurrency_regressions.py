"""Deterministic regressions for storage concurrency and deletion safety."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlmodel import Session

from ..models import DeletionPreview, LogEvent, RetentionPolicy
from ..storage.base import BaseLogStorage
from ..storage.rdbms import EventRecord, SQLRepository
from ..storage.zodb import AnnotationRepository
from .postgres import database_url
from .test_storage_rdbms import Context as RdbmsContext
from .test_storage_zodb import AnnotationStore
from .test_storage_zodb import Context as ZodbContext


class StorageRegressionMixin:
    """Assertions shared by the annotation and relational repositories."""

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def event(
        self,
        comment: str,
        created_at: datetime,
        event_id=None,
    ) -> LogEvent:
        return LogEvent(
            comment=comment,
            created_at=created_at,
            event_id=event_id or uuid4(),
            actor="manager",
            event_type="regression",
            target="context",
            details={"source": "storage-regression"},
        )

    def append(self, comment: str, created_at: datetime, event_id=None):
        return self.repository.append(self.event(comment, created_at, event_id))

    def test_concurrent_appends_keep_one_integrity_chain(self):
        """Two appends racing for one head must not create sibling links."""
        barrier = Barrier(2)
        calls = 0
        calls_lock = Lock()
        original_last_digest = BaseLogStorage.last_digest

        def synchronized_last_digest(storage):
            nonlocal calls
            digest = original_last_digest(storage)
            with calls_lock:
                calls += 1
                wait_for_race = calls <= 2
            if wait_for_race:
                barrier.wait(timeout=10)
            return digest

        first_time = self.now
        second_time = self.now + timedelta(seconds=1)
        with (
            patch.object(BaseLogStorage, "last_digest", synchronized_last_digest),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = (
                executor.submit(self.append, "concurrent-first", first_time),
                executor.submit(self.append, "concurrent-second", second_time),
            )
            for future in futures:
                future.result(timeout=15)
            calls = 2
            self.assertEqual(
                synchronized_last_digest(self.repository),
                original_last_digest(self.repository),
            )

        entries = self.repository.events()
        self.assertEqual(
            [entry["comment"] for entry in entries],
            ["concurrent-first", "concurrent-second"],
        )
        self.assertEqual(entries[0]["previous_digest"], "")
        self.assertEqual(entries[1]["previous_digest"], entries[0]["integrity_digest"])
        self.assertEqual(self.repository.last_digest(), entries[-1]["integrity_digest"])

    def test_backdated_append_rebuilds_the_timestamp_ordered_chain(self):
        """Inserting an old event keeps links in canonical chronological order."""
        self.append("first", self.now)
        self.append("backdated", self.now - timedelta(days=1))
        self.append("last", self.now + timedelta(days=1))

        entries = self.repository.events()
        self.assertEqual(
            [entry["comment"] for entry in entries],
            ["backdated", "first", "last"],
        )
        self.assertEqual(
            [entry["previous_digest"] for entry in entries],
            ["", entries[0]["integrity_digest"], entries[1]["integrity_digest"]],
        )

    def test_replaying_a_successful_deletion_preview_is_rejected(self):
        """A preview is single-use, rather than a replayable delete token."""
        self.append("expired", self.now - timedelta(days=400))
        policy = RetentionPolicy(enabled=True, older_than_days=365)
        preview = self.repository.preview_delete(policy, self.now)

        first = self.repository.delete_preview(preview, "retention policy cleanup")
        self.assertEqual((first.requested, first.deleted, first.missing), (1, 1, 0))
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.repository.delete_preview(preview, "retention policy cleanup")

    def test_forged_event_ids_in_a_preview_are_rejected(self):
        """Changing selected IDs without changing the digest cannot delete rows."""
        self.append("expired", self.now - timedelta(days=400))
        protected = self.append("protected", self.now)
        policy = RetentionPolicy(enabled=True, older_than_days=365)
        preview = self.repository.preview_delete(policy, self.now)
        forged = DeletionPreview(
            operation_id=preview.operation_id,
            object_uid=preview.object_uid,
            cutoff=preview.cutoff,
            event_ids=(UUID(protected["uuid"]),),
            selection_digest=preview.selection_digest,
        )

        with self.assertRaisesRegex(ValueError, "missing or stale"):
            self.repository.delete_preview(forged, "retention policy cleanup")
        self.assertEqual(
            [entry["comment"] for entry in self.repository.events()],
            ["expired", "protected"],
        )

    def test_same_event_uuid_is_scoped_to_each_object(self):
        """Appending one UUID to two objects must retain both rows."""
        shared_id = uuid4()
        self.append("first object", self.now, shared_id)
        other = self.make_other_repository()
        other.append(self.event("second object", self.now, shared_id))

        first = self.repository.get(shared_id)
        second = other.get(shared_id)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["comment"], "first object")
        self.assertEqual(second["comment"], "second object")
        self.assertEqual(len(self.repository.events()), 1)
        self.assertEqual(len(other.events()), 1)


class ZodbStorageRegressionTests(StorageRegressionMixin, TestCase):
    """Regression tests using the existing in-memory annotation fixture."""

    counter = 0

    def setUp(self):
        self.stores = {}
        patcher = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            side_effect=self._annotations,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        type(self).counter += 1
        self.context = ZodbContext(f"storage-regression-{type(self).counter}")
        self.repository = AnnotationRepository(self.context)

    def _annotations(self, context):
        return self.stores.setdefault(id(context), AnnotationStore())

    def make_other_repository(self):
        type(self).counter += 1
        context = ZodbContext(f"storage-regression-{type(self).counter}")
        return AnnotationRepository(context)


class RdbmsStorageRegressionTests(StorageRegressionMixin, TestCase):
    """Regression tests using the existing PostgreSQL container fixture."""

    counter = 0

    def setUp(self):
        self.url = database_url()
        type(self).counter += 1
        self.context = RdbmsContext(f"storage-regression-{type(self).counter}")
        self.repository = SQLRepository(self.context, database_url=self.url)
        self.repository.clear()
        self.addCleanup(self.repository.clear)

    def make_other_repository(self):
        type(self).counter += 1
        context = RdbmsContext(f"storage-regression-{type(self).counter}")
        repository = SQLRepository(context, database_url=self.url)
        repository.clear()
        self.addCleanup(repository.clear)
        return repository

    def test_page_and_count_use_one_consistent_snapshot(self):
        """A writer between page and count queries cannot change the result."""
        self.append("initial", self.now)
        original_exec = Session.exec
        calls = 0
        inserted_id = str(uuid4())

        def exec_and_insert(session, statement, *args, **kwargs):
            nonlocal calls
            result = original_exec(session, statement, *args, **kwargs)
            calls += 1
            if calls == 1:
                with Session(self.repository.engine) as writer, writer.begin():
                    writer.add(
                        EventRecord(
                            event_id=inserted_id,
                            object_uid=self.repository.uid,
                            created_at=self.now + timedelta(seconds=1),
                            comment="concurrent writer",
                        )
                    )
            return result

        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.Session.exec",
            new=exec_and_insert,
        ):
            result = self.repository.search(offset=0, limit=1)

        self.assertEqual(result.total, 1)
        self.assertEqual([entry["comment"] for entry in result.rows], ["initial"])


def test_suite():
    """Register every regression with the canonical zope-testrunner."""
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(ZodbStorageRegressionTests))
    suite.addTest(loader.loadTestsFromTestCase(RdbmsStorageRegressionTests))
    return suite
