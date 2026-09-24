"""Contract tests for the ZODB annotation storage backend."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from BTrees.OOBTree import OOBTree
from persistent import Persistent
from persistent.mapping import PersistentMapping

from ..migrations.v1 import migrate_annotations
from ..models import LogEvent, RetentionPolicy, Severity
from ..storage.zodb import (
    JOURNAL_KEY,
    LOG_KEY,
    POLICY_KEY,
    PREVIEW_KEY,
    AnnotationRepository,
)
from .storage_contract import StorageContractMixin, normalize


class Context(Persistent):
    """Minimal stand-in for a Plone content object."""

    def __init__(self, name="context"):
        self.__name__ = name

    def absolute_url(self):
        return f"https://example.test/{self.__name__}"


class AnnotationStore(dict):
    """dict with the ``_p_changed`` flag ZODB persistent mappings expose."""

    _p_changed = False


class ZodbStorageContractTests(StorageContractMixin, unittest.TestCase):
    """Run the shared storage contract against the ZODB backend."""

    counter = 0

    def setUp(self):
        self.stores: dict[int, AnnotationStore] = {}
        patcher = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            side_effect=self._annotations,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def _annotations(self, context):
        return self.stores.setdefault(id(context), AnnotationStore())

    def make_context(self):
        type(self).counter += 1
        return Context(f"context-{type(self).counter}")

    def make_repository(self, context):
        return AnnotationRepository(context)

    def annotations(self):
        return self.stores.setdefault(id(self.context), AnnotationStore())

    # -- backend specific behaviour ----------------------------------------
    def test_records_are_kept_in_the_object_annotations(self):
        entry = self.append(comment="annotated")
        annotations = self.annotations()
        store = annotations[LOG_KEY]
        self.assertIsInstance(store, OOBTree)
        self.assertIs(store, self.repository.annotations)
        self.assertIn(str(entry["uuid"]), store)
        self.assertIs(store[str(entry["uuid"])], entry)

    def test_legacy_records_are_migrated_by_explicit_upgrade(self):
        annotations = self.annotations()
        legacy_id = uuid4()
        annotations[LOG_KEY] = AnnotationStore(
            {
                datetime(2020, 1, 1): {
                    "uuid": str(legacy_id),
                    "date": datetime(2020, 1, 1, tzinfo=UTC),
                    "comment": "legacy",
                    "username": "old-user",
                    "level": "warn",
                    "details_raw": {"a": 1},
                }
            }
        )

        events = self.repository.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], annotations[LOG_KEY][datetime(2020, 1, 1)])
        self.assertEqual(migrate_annotations(annotations), 1)
        events = self.repository.events()
        # Migrated to the current schema and re-keyed by the event id.
        self.assertEqual(events[0]["event_id"], str(legacy_id))
        self.assertEqual(events[0]["actor"], "old-user")
        self.assertEqual(events[0]["severity"], "warn")
        self.assertEqual(events[0]["schema_version"], 1)
        self.assertTrue(events[0]["integrity_digest"])
        self.assertIn(str(legacy_id), self.annotations()[LOG_KEY])

    def test_legacy_dated_keys_are_deleted_by_retention(self):
        annotations = self.annotations()
        legacy_id = uuid4()
        annotations[LOG_KEY] = AnnotationStore(
            {
                datetime(2020, 1, 1): {
                    "uuid": str(legacy_id),
                    "date": datetime(2020, 1, 1, tzinfo=UTC),
                    "comment": "legacy",
                }
            }
        )
        policy = RetentionPolicy(enabled=True, older_than_days=365)
        self.repository.preview_delete(policy, self.now)
        preview = self.repository.preview_delete(policy, self.now)
        result = self.repository.delete_preview(preview, "remove legacy entries")
        self.assertEqual((result.deleted, result.missing), (1, 0))
        self.assertEqual(self.annotations()[LOG_KEY], {})

    def test_non_dict_annotation_values_are_ignored(self):
        self.annotations()[LOG_KEY] = AnnotationStore(
            {"broken": "not a record", "also-broken": object()}
        )
        self.assertEqual(self.repository.events(), [])
        self.assertIsNone(self.repository.get("broken"))

    def test_annotations_are_created_lazily(self):
        annotations = self.annotations()
        self.assertNotIn(LOG_KEY, annotations)
        self.repository.events()
        self.assertIn(LOG_KEY, annotations)


class ZodbSpecificTests(unittest.TestCase):
    """Behaviour that only the ZODB backend can have."""

    def setUp(self):
        self.store = AnnotationStore()
        patcher = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=self.store,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.context = Context()
        self.repository = AnnotationRepository(self.context)
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def test_annotation_keys_are_unchanged(self):
        self.assertEqual(LOG_KEY, "zopyx.plone.persistentlogger.connector.log")
        self.assertEqual(
            JOURNAL_KEY, "zopyx.plone.persistentlogger.connector.governance"
        )
        self.assertEqual(POLICY_KEY, "zopyx.plone.persistentlogger.connector.retention")
        self.assertEqual(PREVIEW_KEY, "zopyx.plone.persistentlogger.connector.previews")

    def test_journal_and_policy_use_persistent_mappings(self):
        self.repository.record_governance("action", "manager", "reason")
        self.repository.set_policy(RetentionPolicy(enabled=True))
        journal = self.store[JOURNAL_KEY]
        policy = self.store[POLICY_KEY]
        self.assertIsInstance(journal, PersistentMapping)
        self.assertIsInstance(policy, PersistentMapping)
        self.assertEqual(len(journal), 1)
        self.assertEqual(policy["older_than_days"], 365)
        self.assertIs(policy, self.store[POLICY_KEY])

    def test_public_wipe_is_unavailable(self):
        self.repository.append(LogEvent(comment="entry", created_at=self.now))
        self.assertFalse(hasattr(self.repository, "clear"))
        self.assertIsInstance(self.store[LOG_KEY], OOBTree)
        self.assertEqual(len(self.store[LOG_KEY]), 1)
        self.assertEqual(self.repository.events()[0]["comment"], "entry")

    def test_previews_are_stored_in_annotations(self):
        preview = self.repository.preview_delete(RetentionPolicy(), self.now)
        self.assertIn(str(preview.operation_id), self.store[PREVIEW_KEY])
        self.assertIsNone(self.repository.get_preview("missing"))

    def test_object_uid_uses_the_uid_when_available(self):
        self.assertEqual(self.repository.object_uid(), "https://example.test/context")
        with patch(
            "plone.uuid.interfaces.IUUID", side_effect=lambda ctx, default: "uid-1"
        ):
            self.assertEqual(self.repository.object_uid(), "uid-1")

    def test_severity_enum_is_kept_in_the_record(self):
        self.repository.append(
            LogEvent(comment="warn", severity=Severity.WARNING, created_at=self.now)
        )
        entry = self.repository.events()[0]
        self.assertEqual(entry["severity"], Severity.WARNING)
        self.assertEqual(entry["level"], "warning")
        self.assertEqual(normalize(entry)["severity"], "warning")

    def test_legacy_entry_without_uuid_is_reachable(self):
        legacy = {"comment": "no uuid", "date": datetime(2021, 1, 1, tzinfo=UTC)}
        self.store[LOG_KEY] = AnnotationStore({datetime(2021, 1, 1): legacy})
        migrate_annotations(self.store)
        entry = self.repository.events()[0]
        self.assertIsNotNone(self.repository.get(entry["event_id"]))

    def test_delete_ignores_events_of_other_objects(self):
        other_store = AnnotationStore()
        with patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=other_store,
        ):
            other = AnnotationRepository(Context("other"))
            other.append(
                LogEvent(comment="other", created_at=self.now - timedelta(days=400))
            )
            preview = other.preview_delete(
                RetentionPolicy(enabled=True, older_than_days=365), self.now
            )
            result = other.delete_preview(preview, "retention policy cleanup")
            self.assertEqual((result.deleted, result.missing), (1, 0))
            self.assertEqual(other.events(), [])
        self.assertEqual(self.repository.events(), [])
        self.assertNotIn(JOURNAL_KEY, self.store)


class ZodbEdgeCaseTests(unittest.TestCase):
    """Annotation specific corner cases of the ZODB backend."""

    def setUp(self):
        self.context = Context("edge")
        self.store = AnnotationStore()
        patcher = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            side_effect=lambda context: self.store,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repository = AnnotationRepository(self.context)
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_lookup_survives_an_unusable_key(self):
        # an unhashable identifier cannot be looked up in the annotation
        # store and must not propagate the error
        self.assertIsNone(self.repository._load_event(["not", "hashable"]))

    def test_delete_removes_records_under_a_legacy_key(self):
        event = LogEvent(
            comment="legacy",
            created_at=self.now,
            severity=Severity.INFO,
            actor="manager",
            event_type="application",
            target="context",
            details={},
        )
        entry = self.repository.append(event)
        store = self.repository.annotations
        # a record written by the legacy logger keeps its own annotation key
        store["legacy-key"] = store.pop(entry["uuid"])
        deleted, missing = self.repository._delete_events(
            (uuid4(), UUID(entry["uuid"]))
        )
        self.assertEqual((deleted, missing), (1, 1))
        self.assertNotIn("legacy-key", store)


def test_suite():
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(ZodbStorageContractTests))
    suite.addTest(loader.loadTestsFromTestCase(ZodbSpecificTests))
    suite.addTest(loader.loadTestsFromTestCase(ZodbEdgeCaseTests))
    return suite
