"""Contract tests for the RDBMS storage backend.

The very same contract that runs against the ZODB backend is executed here
against a PostgreSQL server started in a test container, which proves that
both backends behave identically.  Without a container runtime the module
skips itself.
"""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine, select

from ..models import LogEvent, RetentionPolicy
from ..storage.base import verify_event_chain
from ..storage.rdbms import (
    EventRecord,
    GovernanceRecord,
    PolicyRecord,
    PreviewRecord,
    SQLRepository,
    _as_utc,
    check_connection,
    dispose_engines,
    get_engine,
)
from .postgres import database_url
from .storage_contract import StorageContractMixin


class Context:
    """Minimal stand-in for a Plone content object."""

    def __init__(self, name="context"):
        self.__name__ = name

    def absolute_url(self):
        return f"https://example.test/{self.__name__}"


class RdbmsStorageContractTests(StorageContractMixin, unittest.TestCase):
    """Run the shared storage contract against PostgreSQL."""

    counter = 0

    def setUp(self):
        # Starts the shared container on first use and skips the test when
        # no container runtime is available.
        self.url = database_url()
        super().setUp()

    def make_context(self):
        type(self).counter += 1
        # A fresh object per test keeps the tests independent of each other.
        return Context(f"contract-{type(self).counter}")

    def make_repository(self, context):
        return SQLRepository(context, database_url=self.url)

    # -- backend specific behaviour ----------------------------------------
    def test_schema_is_created_with_one_table_per_record_type(self):
        engine = get_engine(self.url)
        tables = set(inspect(engine).get_table_names())
        for table in (
            EventRecord,
            GovernanceRecord,
            PolicyRecord,
            PreviewRecord,
        ):
            self.assertIn(table.__tablename__, tables)

    def test_rows_carry_the_object_identifier(self):
        entry = self.append(comment="scoped")
        engine = get_engine(self.url)
        with engine.connect() as connection:
            uids = (
                connection.execute(
                    select(EventRecord.object_uid).where(
                        EventRecord.event_id == entry["uuid"]
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(uids, [self.repository.object_uid()])

    def test_governance_payload_is_stored_in_a_json_column(self):
        entry = self.repository.record_governance(
            "retention_delete", "manager", "cleanup", deleted=3, missing=1
        )
        engine = get_engine(self.url)
        with engine.connect() as connection:
            row = connection.execute(
                select(GovernanceRecord.payload).where(
                    GovernanceRecord.event_id == entry["event_id"]
                )
            ).one()
        self.assertEqual(row[0], {"deleted": 3, "missing": 1})

    def test_naive_timestamps_are_read_back_as_utc(self):
        entry = self.append(created_at=datetime(2026, 1, 1, tzinfo=UTC), comment="utc")
        stored = self.repository.get(entry["uuid"])
        self.assertEqual(stored["created_at"], datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(stored["created_at"].utcoffset().total_seconds(), 0)

    def test_details_must_be_json_serializable(self):
        with self.assertRaises(ValueError):
            self.append(comment="not serializable", details={"bad": object()})


class RdbmsSpecificTests(unittest.TestCase):
    """Behaviour that only the RDBMS backend can have."""

    def setUp(self):
        self.url = database_url()
        self.context = Context("rdbms-specific")
        self.repository = SQLRepository(self.context, database_url=self.url)
        self.repository.clear()
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def tearDown(self):
        self.repository.clear()

    def test_engine_is_cached_per_url(self):
        self.assertIs(get_engine(self.url), get_engine(self.url))
        dispose_engines()
        # Engines are recreated on demand after being disposed.
        self.assertIsNotNone(get_engine(self.url))
        self.assertIs(get_engine(self.url), get_engine(self.url))

    def test_repository_requires_a_database_url(self):
        from ..storage.base import StorageConfigurationError

        with self.assertRaises(StorageConfigurationError):
            SQLRepository(self.context)

    def test_engine_can_be_injected(self):
        repository = SQLRepository(self.context, engine=get_engine(self.url))
        repository.append(LogEvent(comment="injected", created_at=self.now))
        self.assertEqual(
            [entry["comment"] for entry in repository.events()], ["injected"]
        )

    def test_records_are_scoped_by_object_uid(self):
        other = SQLRepository(Context("other"), database_url=self.url)
        self.repository.append(LogEvent(comment="mine", created_at=self.now))
        self.assertEqual(other.events(), [])
        self.assertEqual(other.journal(), [])
        self.assertEqual(other.policy(), RetentionPolicy())

    def test_schema_creation_is_idempotent(self):
        engine = get_engine(self.url)
        from ..storage.rdbms import Base

        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        self.assertIn(EventRecord.__tablename__, tables)


class RdbmsHelperTests(unittest.TestCase):
    """Helpers of the backend that are not part of the shared contract."""

    def setUp(self):
        self.url = database_url()

    def test_check_connection_succeeds_against_the_container(self):
        check_connection(self.url)

    def test_plain_timestamps_are_treated_as_utc(self):
        plain = datetime(2026, 1, 1, 12, 0, 0)
        self.assertEqual(_as_utc(plain), plain.replace(tzinfo=UTC))
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        self.assertEqual(_as_utc(aware), aware)


class RdbmsSQLiteHardeningTests(unittest.TestCase):
    """Exercise storage invariants without requiring a PostgreSQL container."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{self.tmp.name}/storage.db",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_uuid_can_be_reused_by_another_object_without_merging_rows(self):
        event_id = uuid4()
        first = SQLRepository(Context("first"), engine=self.engine)
        second = SQLRepository(Context("second"), engine=self.engine)
        first.append(LogEvent(comment="first", event_id=event_id))
        second.append(LogEvent(comment="second", event_id=event_id))
        self.assertEqual([row["comment"] for row in first.events()], ["first"])
        self.assertEqual([row["comment"] for row in second.events()], ["second"])

    def test_duplicate_uuid_on_one_object_is_rejected(self):
        event_id = uuid4()
        repository = SQLRepository(Context("same"), engine=self.engine)
        repository.append(LogEvent(comment="first", event_id=event_id))
        with self.assertRaises(ValueError):
            repository.append(LogEvent(comment="duplicate", event_id=event_id))

    def test_search_page_and_count_share_the_same_scope_and_filters(self):
        repository = SQLRepository(Context("search"), engine=self.engine)
        for comment in ("alpha", "beta", "alphabet"):
            repository.append(LogEvent(comment=comment))
        result = repository.search(quick="alpha", offset=1, limit=1)
        self.assertEqual(result.total, 2)
        self.assertEqual(len(result.rows), 1)

    def test_concurrent_appends_have_one_deterministic_chain(self):
        repositories = [
            SQLRepository(Context("concurrent"), engine=self.engine) for _ in range(8)
        ]

        def append(index):
            return repositories[index].append(LogEvent(comment=f"event-{index}"))

        with ThreadPoolExecutor(max_workers=8) as executor:
            returned = list(executor.map(append, range(8)))
        stored = repositories[0].events()
        self.assertEqual(len(stored), 8)
        self.assertEqual(len({row["previous_digest"] for row in stored}), 8)
        self.assertTrue(verify_event_chain(stored))
        self.assertEqual(
            {row["uuid"] for row in returned}, {row["uuid"] for row in stored}
        )


def test_suite():
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(RdbmsStorageContractTests))
    suite.addTest(loader.loadTestsFromTestCase(RdbmsSpecificTests))
    suite.addTest(loader.loadTestsFromTestCase(RdbmsHelperTests))
    suite.addTest(loader.loadTestsFromTestCase(RdbmsSQLiteHardeningTests))
    return suite
