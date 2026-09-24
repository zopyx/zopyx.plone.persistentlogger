"""Backend independent contract tests for the audit log storage layer.

Every test in :class:`StorageContractMixin` runs unchanged against the ZODB
annotation backend and against the RDBMS backend, which is what guarantees
that both implementations behave identically.  A backend specific test class
only provides :meth:`~StorageContractMixin.make_context` and
:meth:`~StorageContractMixin.make_repository`; the assertions live here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from ..models import DeletionPreview, LogEvent, RetentionPolicy, Severity
from ..serialization import canonical_json
from ..storage.base import (
    StorageIntegrityError,
    verify_event_chain,
    verify_governance_chain,
)
from ..storage.query import ConditionGroup, SortSpec, parse_filter_model


def normalize(value):
    """Reduce a record to JSON primitives so backend results are comparable.

    The ZODB backend keeps Python objects (a ``Severity`` member, an aware
    ``datetime``) in the record, the RDBMS backend returns the values its
    columns hold.  Normalizing through the canonical JSON encoding makes the
    two representations directly comparable without hiding differences.
    """
    return json.loads(canonical_json(value))


class StorageContractMixin:
    """Contract that every storage backend has to fulfil."""

    # -- provided by the concrete test class -------------------------------
    def make_context(self):
        raise NotImplementedError

    def make_repository(self, context):
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------
    def setUp(self):
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.context = self.make_context()
        self.repository = self.make_repository(self.context)

    def event(self, created_at=None, comment="event", **kwargs):
        options = {
            "severity": Severity.INFO,
            "actor": "manager",
            "event_type": "governance",
            "target": "context",
            "details": {"source": "test"},
        }
        options.update(kwargs)
        return LogEvent(comment=comment, created_at=created_at or self.now, **options)

    def append(self, created_at=None, comment="event", **kwargs):
        return self.repository.append(self.event(created_at, comment, **kwargs))

    def record(self, comment):
        """Return the stored record with the given comment."""
        return next(
            entry for entry in self.repository.events() if entry["comment"] == comment
        )

    # -- empty repository --------------------------------------------------
    def test_new_repository_is_empty(self):
        self.assertEqual(self.repository.events(), [])
        self.assertEqual(self.repository.journal(), [])
        self.assertEqual(self.repository.last_digest(), "")
        self.assertIsNone(self.repository.get(str(uuid4())))
        self.assertIsNone(self.repository.get_preview(str(uuid4()), self.now))

    def test_default_policy_is_disabled(self):
        policy = self.repository.policy()
        self.assertEqual(policy, RetentionPolicy())
        self.assertFalse(policy.enabled)

    # -- events ------------------------------------------------------------
    def test_append_returns_canonical_record(self):
        event = self.event(comment="created")
        entry = self.repository.append(event)
        self.assertEqual(
            sorted(entry),
            [
                "actor",
                "comment",
                "created_at",
                "date",
                "details",
                "details_raw",
                "event_id",
                "event_type",
                "info_url",
                "integrity_digest",
                "level",
                "previous_digest",
                "schema_version",
                "sequence",
                "severity",
                "target",
                "username",
                "uuid",
            ],
        )
        self.assertEqual(entry["uuid"], str(event.event_id))
        self.assertEqual(entry["event_id"], str(event.event_id))
        self.assertEqual(entry["created_at"], self.now)
        self.assertEqual(entry["date"], self.now)
        self.assertEqual(entry["comment"], "created")
        self.assertEqual(entry["actor"], "manager")
        self.assertEqual(entry["username"], "manager")
        self.assertEqual(str(entry["level"]), "info")
        self.assertEqual(str(entry["severity"]), "info")
        self.assertEqual(entry["event_type"], "governance")
        self.assertEqual(entry["target"], "context")
        self.assertIsNone(entry["info_url"])
        self.assertEqual(entry["schema_version"], 1)
        self.assertEqual(entry["sequence"], 1)
        self.assertTrue(entry["integrity_digest"])
        self.assertEqual(entry["previous_digest"], "")

    def test_append_preserves_event_id_and_timestamp(self):
        event = self.event(created_at=self.now - timedelta(days=3))
        self.repository.append(event)
        stored = self.repository.get(event.event_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["uuid"], str(event.event_id))
        self.assertEqual(stored["date"], self.now - timedelta(days=3))

    def test_append_builds_integrity_chain(self):
        # The chain follows append order.  ``events()`` still presents
        # records by timestamp for display and query consumers.
        first = self.append(self.now - timedelta(minutes=3), "first")
        second = self.append(self.now - timedelta(minutes=2), "second")
        third = self.append(self.now - timedelta(minutes=1), "third")
        self.assertEqual(first["previous_digest"], "")
        self.assertEqual(second["previous_digest"], first["integrity_digest"])
        self.assertEqual(third["previous_digest"], second["integrity_digest"])
        self.assertEqual(self.repository.last_digest(), third["integrity_digest"])
        self.assertEqual(
            len({first["integrity_digest"], second["integrity_digest"]}), 2
        )

    def test_append_keeps_existing_records_immutable(self):
        """Later and backdated appends do not rewrite prior digests."""
        first = self.append(self.now, "first")
        second = self.append(self.now + timedelta(days=1), "second")
        before = {
            entry["uuid"]: (entry["previous_digest"], entry["integrity_digest"])
            for entry in self.repository.events()
        }
        with patch.object(self.repository, "_rewrite_event") as rewrite:
            self.append(self.now - timedelta(days=1), "backdated")
        rewrite.assert_not_called()
        for entry in self.repository.events():
            if entry["uuid"] in before:
                self.assertEqual(
                    (entry["previous_digest"], entry["integrity_digest"]),
                    before[entry["uuid"]],
                )
        self.assertEqual(first["previous_digest"], "")
        self.assertEqual(second["previous_digest"], first["integrity_digest"])
        self.assertTrue(verify_event_chain(self.repository.events()))

    def test_append_rejects_a_missing_predecessor(self):
        first = self.append(self.now, "first")
        self.append(self.now + timedelta(days=1), "second")
        deleted, missing = self.repository._delete_events((UUID(first["uuid"]),))
        self.assertEqual((deleted, missing), (1, 0))
        with self.assertRaises(StorageIntegrityError):
            self.append(self.now + timedelta(days=2), "third")

    def test_events_are_ordered_by_timestamp(self):
        newest = self.append(self.now, "newest")
        oldest = self.append(self.now - timedelta(days=2), "oldest")
        middle = self.append(self.now - timedelta(days=1), "middle")
        self.assertEqual(
            [entry["uuid"] for entry in self.repository.events()],
            [oldest["uuid"], middle["uuid"], newest["uuid"]],
        )

    def test_events_are_isolated_per_object(self):
        self.append(self.now - timedelta(minutes=1), "mine")
        other = self.make_repository(self.make_context())
        self.assertEqual(other.events(), [])
        self.append(self.now, "second")
        self.assertEqual(
            [entry["comment"] for entry in self.repository.events()],
            ["mine", "second"],
        )
        self.assertEqual(other.events(), [])

    def test_details_round_trip(self):
        self.append(
            comment="with details",
            details={"count": 2, "nested": {"flag": True}, "when": self.now},
        )
        entry = normalize(self.record("with details"))
        expected = {"count": 2, "nested": {"flag": True}, "when": self.now.isoformat()}
        self.assertEqual(entry["details"], expected)
        self.assertEqual(entry["details_raw"], expected)

    def test_append_without_details_stores_none(self):
        self.append(comment="plain", details=None)
        self.assertIsNone(self.record("plain")["details"])
        self.assertIsNone(self.record("plain")["details_raw"])

    def test_get_returns_none_for_unknown_id(self):
        self.append(comment="known")
        self.assertIsNone(self.repository.get(str(uuid4())))
        self.assertIsNone(self.repository.get("not-a-uuid"))

    def test_public_wipe_is_unavailable(self):
        policy = RetentionPolicy(enabled=True, older_than_days=30, max_entries=5)
        self.repository.set_policy(policy)
        entry = self.append(comment="retained")
        self.repository.record_governance(
            "retention_policy_changed", "manager", "retain the configured policy"
        )
        self.assertFalse(hasattr(self.repository, "clear"))
        self.assertEqual(self.repository.get(entry["uuid"]), entry)
        self.assertEqual(self.repository.events(), [entry])
        self.assertEqual(self.repository.policy(), policy)
        self.assertEqual(len(self.repository.journal()), 1)

    # -- policy ------------------------------------------------------------
    def test_policy_round_trip_and_update(self):
        first = RetentionPolicy(enabled=True, older_than_days=30, max_entries=5)
        self.repository.set_policy(first)
        self.assertEqual(self.repository.policy(), first)
        second = RetentionPolicy(enabled=False, older_than_days=10, max_entries=1)
        self.repository.set_policy(second)
        self.assertEqual(self.repository.policy(), second)

    def test_policy_is_isolated_per_object(self):
        self.repository.set_policy(
            RetentionPolicy(enabled=True, older_than_days=30, max_entries=5)
        )
        other = self.make_repository(self.make_context())
        self.assertEqual(other.policy(), RetentionPolicy())

    # -- retention previews ------------------------------------------------
    def test_preview_delete_selects_expired_events(self):
        expired = self.append(self.now - timedelta(days=400), "expired")
        self.append(self.now - timedelta(days=10), "recent")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        self.assertEqual([str(value) for value in preview.event_ids], [expired["uuid"]])
        self.assertEqual(preview.object_uid, self.repository.object_uid())
        self.assertEqual(preview.cutoff, self.now - timedelta(days=365))
        self.assertTrue(preview.selection_digest)

    def test_preview_delete_respects_max_entries(self):
        for index in range(4):
            self.append(self.now - timedelta(days=400 - index), f"expired-{index}")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365, max_entries=2),
            self.now,
        )
        self.assertEqual(
            [str(value) for value in preview.event_ids],
            [self.record(f"expired-{index}")["uuid"] for index in range(2)],
        )

    def test_preview_delete_without_eligible_events(self):
        self.append(self.now, "recent")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        self.assertEqual(preview.event_ids, ())
        result = self.repository.delete_preview(preview, "nothing to delete")
        self.assertEqual((result.deleted, result.missing, result.requested), (0, 0, 0))

    def test_preview_is_stored_and_retrievable(self):
        self.append(self.now - timedelta(days=400), "expired")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        stored = self.repository.get_preview(preview.operation_id)
        self.assertEqual(stored, preview)
        self.assertIsNone(self.repository.get_preview(str(uuid4()), self.now))

    def test_preview_is_isolated_per_object(self):
        preview = self.repository.preview_delete(RetentionPolicy(), self.now)
        other = self.make_repository(self.make_context())
        self.assertIsNone(other.get_preview(preview.operation_id))

    # -- deletion ----------------------------------------------------------
    def test_delete_preview_requires_a_substantial_reason(self):
        preview = self.repository.preview_delete(RetentionPolicy(), self.now)
        with self.assertRaises(ValueError):
            self.repository.delete_preview(preview, "short")

    def test_delete_preview_rejects_unknown_preview(self):
        with self.assertRaises(ValueError):
            self.repository.delete_preview(
                DeletionPreview(uuid4(), "object", self.now, (), "digest"),
                "a sufficiently long reason",
            )

    def test_delete_preview_rejects_stale_preview(self):
        self.append(self.now - timedelta(days=400), "expired")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        stale = DeletionPreview(
            preview.operation_id,
            preview.object_uid,
            preview.cutoff,
            preview.event_ids,
            "tampered",
        )
        with self.assertRaises(ValueError):
            self.repository.delete_preview(stale, "a sufficiently long reason")
        self.assertEqual(len(self.repository.events()), 1)

    def test_delete_preview_uses_stored_selection_not_caller_ids(self):
        expired = self.append(self.now - timedelta(days=400), "expired")
        keep = self.append(self.now, "keep")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        forged = DeletionPreview(
            preview.operation_id,
            preview.object_uid,
            preview.cutoff,
            (UUID(keep["uuid"]),),
            preview.selection_digest,
        )
        with self.assertRaises(ValueError):
            self.repository.delete_preview(forged, "retention policy cleanup")
        self.assertEqual(
            [entry["uuid"] for entry in self.repository.events()],
            [expired["uuid"], keep["uuid"]],
        )

    def test_delete_preview_is_one_shot(self):
        self.append(self.now - timedelta(days=400), "expired")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        self.repository.delete_preview(preview, "retention policy cleanup")
        with self.assertRaises(ValueError):
            self.repository.delete_preview(preview, "retention policy cleanup")

    def test_delete_preview_removes_events_and_counts_missing(self):
        expired = self.append(self.now - timedelta(days=400), "expired")
        self.append(self.now, "keep")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        self.repository._delete_events((UUID(expired["uuid"]),))
        self.append(self.now, "recreated")
        result = self.repository.delete_preview(preview, "retention policy cleanup")
        self.assertEqual((result.requested, result.eligible), (1, 1))
        self.assertEqual((result.deleted, result.missing, result.failed), (0, 1, 0))
        self.assertEqual(result.reason, "retention policy cleanup")

    def test_delete_then_verify_preserves_and_anchors_survivors(self):
        """Retention relinks survivors and records a verifiable root anchor."""
        operation_now = datetime.now(UTC)
        self.append(operation_now - timedelta(days=400), "expired")
        self.append(operation_now - timedelta(days=10), "survivor-one")
        self.append(operation_now, "survivor-two")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), operation_now
        )
        result = self.repository.delete_and_journal(
            preview, "retention policy cleanup", "manager"
        )
        self.assertEqual((result.deleted, result.missing), (1, 0))
        events = self.repository.events()
        self.assertTrue(verify_event_chain(events))
        journal = self.repository.journal()
        self.assertTrue(verify_governance_chain(journal))
        anchor = journal[-1]["survivor_digest"]
        self.assertEqual(anchor, self.repository.event_chain_anchor())
        self.assertTrue(verify_event_chain(events, anchor))
        tampered = [dict(entry) for entry in events]
        tampered[0]["comment"] = "tampered survivor"
        self.assertFalse(verify_event_chain(tampered, anchor))

    def test_delete_preview_removes_only_selected_events(self):
        self.append(self.now - timedelta(days=400), "expired")
        self.append(self.now, "recent")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        result = self.repository.delete_preview(preview, "retention policy cleanup")
        self.assertEqual((result.deleted, result.missing), (1, 0))
        self.assertEqual(
            [entry["comment"] for entry in self.repository.events()], ["recent"]
        )

    def test_delete_preview_does_not_touch_other_objects(self):
        other = self.make_repository(self.make_context())
        other.append(self.event(self.now - timedelta(days=400), "other expired"))
        self.append(self.now - timedelta(days=400), "expired")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        self.repository.delete_preview(preview, "retention policy cleanup")
        self.assertEqual(
            [entry["comment"] for entry in other.events()], ["other expired"]
        )

    def test_delete_and_journal_commits_deletion_and_evidence(self):
        self.append(self.now - timedelta(days=400), "expired")
        self.append(self.now, "recent")
        preview = self.repository.preview_delete(
            RetentionPolicy(enabled=True, older_than_days=365), self.now
        )
        result = self.repository.delete_and_journal(
            preview, "retention policy cleanup", "manager"
        )
        self.assertEqual((result.deleted, result.missing, result.failed), (1, 0, 0))
        self.assertEqual(
            [entry["comment"] for entry in self.repository.events()], ["recent"]
        )
        journal = self.repository.journal()
        self.assertEqual(len(journal), 1)
        self.assertEqual(journal[0]["action"], "retention_delete")
        self.assertEqual(journal[0]["deleted"], 1)
        self.assertIsNone(self.repository.get_preview(preview.operation_id))

    # -- governance journal --------------------------------------------------
    def test_record_governance_builds_chain_and_payload(self):
        first = self.repository.record_governance(
            "retention_delete", "manager", "policy cleanup", deleted=2
        )
        second = self.repository.record_governance(
            "retention_delete", "manager", "policy cleanup", deleted=0
        )
        self.assertEqual(first["action"], "retention_delete")
        self.assertEqual(first["actor"], "manager")
        self.assertEqual(first["reason"], "policy cleanup")
        self.assertEqual(normalize(first)["deleted"], 2)
        self.assertEqual(first["previous_digest"], "")
        self.assertEqual(second["previous_digest"], first["integrity_digest"])
        self.assertEqual(len(self.repository.journal()), 2)
        self.assertTrue(verify_governance_chain(self.repository.journal()))

    def test_event_chain_detects_tampering_and_missing_records(self):
        self.append(comment="first")
        self.append(comment="second")
        events = self.repository.events()
        self.assertTrue(verify_event_chain(events))
        self.assertFalse(
            verify_event_chain([dict(events[0], comment="tampered"), events[1]])
        )
        self.assertFalse(verify_event_chain([events[1]]))

    def test_journal_is_reconstructed_from_storage(self):
        entry = self.repository.record_governance(
            "export", "manager", "export requested", format="json"
        )
        stored = self.repository.journal()
        self.assertEqual(len(stored), 1)
        self.assertEqual(normalize(stored[0]), normalize(entry))

    def test_journal_is_ordered_and_isolated(self):
        with patch(
            "zopyx.plone.persistentlogger.storage.base.utc_now",
            side_effect=[self.now, self.now + timedelta(minutes=1)],
        ):
            first = self.repository.record_governance("first", "manager", "one")
            second = self.repository.record_governance("second", "manager", "two")
        self.assertEqual(second["previous_digest"], first["integrity_digest"])
        self.assertEqual(
            [entry["action"] for entry in self.repository.journal()],
            ["first", "second"],
        )
        other = self.make_repository(self.make_context())
        self.assertEqual(other.journal(), [])
        other.record_governance("third", "manager", "three")
        self.assertEqual(
            [entry["action"] for entry in self.repository.journal()],
            ["first", "second"],
        )

    # -- agGrid query model ------------------------------------------------
    def test_search_returns_every_record_in_chronological_order(self):
        for index in range(5):
            self.append(self.now + timedelta(minutes=index), f"entry {index}")
        result = self.repository.search()
        self.assertEqual(result.total, 5)
        self.assertEqual(
            [entry["comment"] for entry in result.rows],
            [f"entry {index}" for index in range(5)],
        )

    def test_search_pages_sorts_and_filters(self):
        for index in range(6):
            self.append(
                self.now + timedelta(minutes=index),
                f"entry {index}",
                severity=Severity.ERROR if index % 2 else Severity.INFO,
                actor=f"user{index}",
                info_url=None if index % 3 == 0 else f"http://example.test/{index}",
            )

        # paging: one page plus the total number of matches
        page = self.repository.search(
            sort=(SortSpec("created_at", True),), offset=1, limit=2
        )
        self.assertEqual(page.total, 6)
        self.assertEqual(
            [entry["comment"] for entry in page.rows], ["entry 4", "entry 3"]
        )

        tail = self.repository.search(offset=4, limit=10)
        self.assertEqual((len(tail.rows), tail.total), (2, 6))
        self.assertEqual(self.repository.search(offset=99, limit=10).rows, ())

        # filtering on an enum like column (set filter)
        errors = self.repository.search(
            conditions=parse_filter_model(
                {"severity": {"filterType": "set", "values": ["error"]}}
            )
        )
        self.assertEqual(errors.total, 3)
        self.assertEqual(
            [entry["severity"] for entry in errors.rows],
            [Severity.ERROR, Severity.ERROR, Severity.ERROR],
        )

        # case insensitive text filter
        text = self.repository.search(
            conditions=parse_filter_model(
                {
                    "comment": {
                        "filterType": "text",
                        "type": "contains",
                        "filter": "ENTRY 5",
                    }
                }
            )
        )
        self.assertEqual([entry["comment"] for entry in text.rows], ["entry 5"])

        # date window (the query model normalises the timestamps)
        window = self.repository.search(
            conditions=parse_filter_model(
                {
                    "created_at": {
                        "filterType": "date",
                        "type": "inRange",
                        "dateFrom": (self.now + timedelta(minutes=1)).isoformat(),
                        "dateTo": (self.now + timedelta(minutes=3)).isoformat(),
                    }
                }
            )
        )
        self.assertEqual(
            [entry["comment"] for entry in window.rows],
            ["entry 1", "entry 2", "entry 3"],
        )

        # the quick filter searches the text columns
        quick = self.repository.search(quick="user3")
        self.assertEqual([entry["comment"] for entry in quick.rows], ["entry 3"])
        self.assertEqual(self.repository.search(quick="nothing").total, 0)

        # blank text values sort first, exactly like the SQL backend orders NULL
        by_url = self.repository.search(sort=(SortSpec("info_url"),), limit=2)
        self.assertEqual([entry["info_url"] for entry in by_url.rows], [None, None])
        by_url_desc = self.repository.search(
            sort=(SortSpec("info_url", True),), limit=2
        )
        self.assertEqual(
            [entry["info_url"] for entry in by_url_desc.rows],
            ["http://example.test/5", "http://example.test/4"],
        )

    def test_search_matches_the_events_of_the_repository(self):
        for index in range(3):
            self.append(self.now + timedelta(minutes=index), f"entry {index}")
        every = self.repository.search(limit=100)
        self.assertEqual(
            [entry["uuid"] for entry in every.rows],
            [entry["uuid"] for entry in self.repository.events()],
        )

    def test_search_breaks_ties_chronologically(self):
        for index in range(3):
            self.append(self.now + timedelta(minutes=index), "same comment")
        result = self.repository.search(
            conditions=parse_filter_model(
                {
                    "comment": {
                        "filterType": "text",
                        "type": "equals",
                        "filter": "same comment",
                    }
                }
            ),
            sort=(SortSpec("comment"),),
        )
        self.assertEqual(result.total, 3)
        # equal on the sort column, so the record order stays chronological
        self.assertEqual(
            [entry["uuid"] for entry in result.rows],
            [entry["uuid"] for entry in self.repository.events()],
        )

    def test_search_supports_every_filter_operator(self):
        for index in range(4):
            self.append(
                self.now + timedelta(minutes=index),
                f"entry {index}",
                actor=f"user{index}",
                info_url=None if index == 3 else f"http://example.test/{index}",
                schema_version=index + 1,
            )

        def search(field, **spec):
            return self.repository.search(conditions=parse_filter_model({field: spec}))

        def total(field, **spec):
            return search(field, **spec).total

        # text operators
        self.assertEqual(
            total("comment", filterType="text", type="equals", filter="entry 2"), 1
        )
        self.assertEqual(
            total("comment", filterType="text", type="notEqual", filter="entry 2"), 3
        )
        self.assertEqual(
            total("comment", filterType="text", type="contains", filter="ENTRY 3"), 1
        )
        self.assertEqual(
            total("comment", filterType="text", type="notContains", filter="entry"), 0
        )
        self.assertEqual(
            total("comment", filterType="text", type="startsWith", filter="entry"), 4
        )
        self.assertEqual(
            total("comment", filterType="text", type="endsWith", filter="ry 1"), 1
        )
        self.assertEqual(total("info_url", filterType="text", type="blank"), 1)
        self.assertEqual(total("info_url", filterType="text", type="notBlank"), 3)
        # the details column is a JSON column
        self.assertEqual(total("details", filterType="text", type="notBlank"), 4)
        self.assertEqual(
            total("details", filterType="text", type="contains", filter="source"), 4
        )
        self.assertEqual(total("details", filterType="text", type="blank"), 0)

        # number operators
        self.assertEqual(
            total("schema_version", filterType="number", type="equals", filter=2), 1
        )
        self.assertEqual(
            total("schema_version", filterType="number", type="notEqual", filter=2), 3
        )
        self.assertEqual(
            total("schema_version", filterType="number", type="lessThan", filter=2), 1
        )
        self.assertEqual(
            total(
                "schema_version", filterType="number", type="lessThanOrEqual", filter=2
            ),
            2,
        )
        self.assertEqual(
            total("schema_version", filterType="number", type="greaterThan", filter=3),
            1,
        )
        self.assertEqual(
            total(
                "schema_version",
                filterType="number",
                type="greaterThanOrEqual",
                filter=3,
            ),
            2,
        )
        self.assertEqual(
            total(
                "schema_version",
                filterType="number",
                type="inRange",
                filter=2,
                filterTo=3,
            ),
            2,
        )
        self.assertEqual(
            total("schema_version", filterType="number", type="inRange", filter=3), 2
        )

        # date operators (the record timestamps are 12:00 .. 12:03)
        def dates(operator, **extra):
            return total("created_at", filterType="date", type=operator, **extra)

        second = (self.now + timedelta(minutes=2)).isoformat()
        self.assertEqual(dates("equals", dateFrom=second), 1)
        self.assertEqual(dates("notEqual", dateFrom=second), 3)
        self.assertEqual(dates("lessThan", dateFrom=second), 2)
        self.assertEqual(dates("lessThanOrEqual", dateFrom=second), 3)
        self.assertEqual(dates("greaterThan", dateFrom=second), 1)
        self.assertEqual(dates("greaterThanOrEqual", dateFrom=second), 2)
        self.assertEqual(
            dates("inRange", dateFrom=self.now.isoformat(), dateTo=second), 3
        )
        # a one sided range is an open ended comparison
        self.assertEqual(dates("inRange", dateFrom=second), 2)
        # a date without a value cannot match anything
        self.assertEqual(dates("equals"), 0)
        # date only bounds cover the whole day
        day = self.now.date().isoformat()
        self.assertEqual(dates("greaterThanOrEqual", dateFrom=day), 4)
        self.assertEqual(dates("lessThanOrEqual", dateFrom=day), 4)
        self.assertEqual(dates("lessThanOrEqual", dateFrom="2025-12-31"), 0)

        # combined filters: OR inside one column, AND across columns
        combined = self.repository.search(
            conditions=parse_filter_model(
                {
                    "comment": {
                        "filterType": "text",
                        "operator": "OR",
                        "condition1": {
                            "filterType": "text",
                            "type": "equals",
                            "filter": "entry 1",
                        },
                        "condition2": {
                            "filterType": "text",
                            "type": "equals",
                            "filter": "entry 2",
                        },
                    },
                    "actor": {
                        "filterType": "text",
                        "operator": "AND",
                        "condition1": {
                            "filterType": "text",
                            "type": "contains",
                            "filter": "user",
                        },
                        "condition2": {
                            "filterType": "text",
                            "type": "notEqual",
                            "filter": "user2",
                        },
                    },
                }
            )
        )
        self.assertEqual([entry["comment"] for entry in combined.rows], ["entry 1"])

        # an empty filter model and an empty group filter nothing
        self.assertEqual(
            self.repository.search(conditions=parse_filter_model({})).total, 4
        )
        self.assertEqual(
            self.repository.search(conditions=ConditionGroup("AND", ())).total, 4
        )

        # paging without a limit, and sorting on a JSON column (all values equal,
        # so the chronological tie break decides)
        self.assertEqual(len(self.repository.search(offset=2).rows), 2)
        self.assertEqual(self.repository.search(offset=4).rows, ())
        by_details = self.repository.search(sort=(SortSpec("details", True),))
        self.assertEqual(
            [entry["uuid"] for entry in by_details.rows],
            [entry["uuid"] for entry in self.repository.events()],
        )
        # an empty page still reports the total
        empty = self.repository.search(offset=10, limit=5)
        self.assertEqual((empty.rows, empty.total), ((), 4))
