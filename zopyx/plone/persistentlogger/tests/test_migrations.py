"""Regression tests for explicit legacy record migration."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from ..migrations.v1 import (
    FALLBACK_DATE,
    QUARANTINE_KEY,
    migrate_annotations,
    migrate_site,
    migrate_store,
)
from ..models import LogEvent
from ..serialization import event_digest as serialized_event_digest
from ..storage.base import event_digest, new_event_entry, verify_event_chain
from ..storage.zodb import LOG_KEY, AnnotationRepository


class AnnotationContext(SimpleNamespace):
    def absolute_url(self):
        return "https://example.test/migration"


class MigrationTests(unittest.TestCase):
    def test_digest_is_identical_for_storage_and_migration_serializers(self):
        event = LogEvent(
            comment="parity",
            actor="operator",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            details={"answer": 42},
        )
        entry = new_event_entry(event)
        self.assertEqual(event_digest(entry), serialized_event_digest(entry))
        self.assertTrue(verify_event_chain([entry]))

    def test_tampering_with_a_migrated_record_is_detected(self):
        record_id = str(uuid4())
        store = {
            datetime(2024, 1, 1): {
                "uuid": record_id,
                "date": datetime(2024, 1, 1),
                "comment": "legacy",
            }
        }
        self.assertEqual(migrate_store(store), 1)
        migrated = list(store.values())
        self.assertTrue(verify_event_chain(migrated))
        migrated[0]["comment"] = "tampered"
        self.assertFalse(verify_event_chain(migrated))

    def test_reading_legacy_records_does_not_write_or_rekey(self):
        record = {"uuid": str(uuid4()), "date": datetime(2020, 1, 1), "comment": "old"}
        raw_store = {datetime(2020, 1, 1): record}
        annotations = {LOG_KEY: raw_store}
        repository = AnnotationRepository(AnnotationContext(__name__="context"))
        with patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=annotations,
        ):
            self.assertEqual(repository.events(), [record])
        self.assertEqual(list(raw_store), [datetime(2020, 1, 1)])
        self.assertEqual(raw_store[datetime(2020, 1, 1)], record)
        self.assertNotIn(QUARANTINE_KEY, annotations)

    def test_missing_and_invalid_dates_use_utc_fallback_ordering(self):
        first = {"uuid": "missing", "comment": "missing date"}
        second = {"uuid": "invalid", "date": "not-a-date", "comment": "invalid date"}
        store = {"first": first, "second": second}
        self.assertEqual(migrate_store(store), 2)
        entries = list(store.values())
        self.assertEqual(
            [entry["event_id"] for entry in entries], ["invalid", "missing"]
        )
        fallback = entries[0]["created_at"]
        self.assertEqual(fallback, FALLBACK_DATE)
        if not isinstance(fallback, datetime):
            self.fail("migration did not normalize the fallback date")
        self.assertIs(fallback.tzinfo, UTC)
        self.assertTrue(verify_event_chain(entries))

    def test_invalid_legacy_record_is_quarantined_and_valid_data_survives(self):
        valid_id = str(uuid4())
        store = {
            "valid": {
                "uuid": valid_id,
                "date": datetime(2024, 1, 1),
                "comment": "valid",
            },
            "broken": {"uuid": "broken", "details_raw": object()},
        }
        quarantine = {}
        changed = migrate_store(store, quarantine)
        self.assertEqual(changed, 2)
        self.assertEqual(list(store), [valid_id])
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(next(iter(quarantine.values()))["original_key"], "'broken'")
        self.assertTrue(verify_event_chain(list(store.values())))

    def test_genericsetup_upgrade_migrates_catalog_objects(self):
        record_id = str(uuid4())
        site = SimpleNamespace()
        content = SimpleNamespace()
        brain = SimpleNamespace(_unrestrictedGetObject=lambda: content)
        site.portal_catalog = SimpleNamespace(unrestrictedSearchResults=lambda: [brain])
        annotations = {
            id(site): {LOG_KEY: {"legacy": {"uuid": record_id, "comment": "site"}}},
            id(content): {
                LOG_KEY: {"legacy": {"uuid": str(uuid4()), "comment": "content"}}
            },
        }

        def get_annotations(obj):
            return annotations[id(obj)]

        with patch(
            "zopyx.plone.persistentlogger.migrations.v1.IAnnotations",
            side_effect=get_annotations,
        ):
            self.assertEqual(migrate_site(SimpleNamespace(getSite=lambda: site)), 2)
        self.assertTrue(verify_event_chain(list(annotations[id(site)][LOG_KEY].values())))
        self.assertTrue(verify_event_chain(list(annotations[id(content)][LOG_KEY].values())))
        self.assertTrue(migrate_annotations(annotations[id(site)]) == 0)


if __name__ == "__main__":
    unittest.main()
