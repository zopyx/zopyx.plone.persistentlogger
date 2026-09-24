"""Unit tests for the backend independent parts of the storage layer.

These cover the template methods and helpers of ``storage/base.py`` that the
backends share, plus the contract mixin itself, which has to stay abstract.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from ..models import LogEvent, RetentionPolicy, Severity
from ..storage.base import BaseLogStorage, object_uid
from .storage_contract import StorageContractMixin


class ObjectUidTests(unittest.TestCase):
    """``object_uid`` prefers the UID, then the URL, then the object name."""

    def test_uid_wins(self):
        self.assertEqual(object_uid(SimpleNamespace(__name__="x")), "x")

    def test_absolute_url_is_used_when_no_uid_exists(self):
        context = SimpleNamespace(__name__="legacy")
        context.absolute_url = lambda: "https://example.test/legacy"
        self.assertEqual(object_uid(context), "https://example.test/legacy")

    def test_object_name_is_the_last_resort(self):
        self.assertEqual(str(object_uid(SimpleNamespace(__name__="named"))), "named")

    def test_a_broken_uid_lookup_falls_back(self):
        context = SimpleNamespace(__name__="broken")
        with patch("plone.uuid.interfaces.IUUID", side_effect=TypeError("boom")):
            self.assertEqual(object_uid(context), "broken")


class StubStorage(BaseLogStorage):
    """Minimal backend that only implements the persistence primitives."""

    def __init__(self, entries=()):
        self.context = SimpleNamespace(__name__="stub")
        self.entries_data = list(entries)

    def _load_events(self):
        return list(self.entries_data)

    def _store_event(self, entry):
        self.entries_data.append(entry)

    def _delete_events(self, event_ids):
        return (len(event_ids), 0)

    def _remove_all_events(self):
        self.entries_data.clear()

    def _load_journal(self):
        return []

    def _store_governance(self, entry):
        return None

    def _load_policy(self):
        return None

    def _store_policy(self, policy):
        return None

    def _store_preview(self, preview):
        return None

    def _load_preview(self, operation_id):
        return None


class BaseLogStorageTests(unittest.TestCase):
    """The generic template methods of the storage contract."""

    def test_lookup_by_id_scans_the_records(self):
        storage = StubStorage([{"uuid": "a", "comment": "first"}])
        # a backend without its own lookup uses the generic scan
        self.assertEqual(storage.get("a"), {"uuid": "a", "comment": "first"})
        self.assertIsNone(storage.get("missing"))

    def test_object_uid_helper_is_shared(self):
        self.assertEqual(StubStorage().object_uid(), "stub")

    def test_last_digest_is_empty_for_an_empty_log(self):
        self.assertEqual(StubStorage().last_digest(), "")

    def test_stub_backend_uses_the_shared_template_methods(self):
        """The template methods work for a backend that only stores records."""
        storage = StubStorage()
        self.assertEqual(storage.policy(), RetentionPolicy())
        storage.set_policy(RetentionPolicy(enabled=True))
        self.assertIsNone(storage.get_preview("missing"))

        event = LogEvent(
            comment="stub",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            severity=Severity.INFO,
            actor="manager",
            event_type="application",
            target="stub",
            details={},
        )
        entry = storage.append(event)
        self.assertEqual((storage.get(entry["uuid"]) or {})["comment"], "stub")
        self.assertEqual(storage.last_digest(), entry["integrity_digest"])

        governance = storage.record_governance("stub", "manager", "reason")
        self.assertEqual(governance["action"], "stub")

        preview = storage.preview_delete(
            RetentionPolicy(enabled=True), datetime(2026, 1, 1, tzinfo=UTC)
        )
        self.assertIsNone(storage.get_preview(str(preview.operation_id)))
        with self.assertRaisesRegex(ValueError, "missing or stale"):
            storage.delete_preview(preview, "retention policy cleanup")

        storage.clear()
        self.assertEqual(storage.events(), [])
        self.assertIsNone(storage.get(entry["uuid"]))


class StorageContractMixinTests(unittest.TestCase):
    """The contract mixin is abstract: a backend has to provide the hooks."""

    def test_context_and_repository_hooks_are_required(self):
        mixin = StorageContractMixin()
        with self.assertRaises(NotImplementedError):
            mixin.make_context()
        with self.assertRaises(NotImplementedError):
            mixin.make_repository(None)
