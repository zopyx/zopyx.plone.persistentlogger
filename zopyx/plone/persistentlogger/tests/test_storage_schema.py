"""Upgrade and lifecycle tests for the external audit database."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine, select

from ..storage import factory as storage_factory
from ..storage import rdbms
from ..storage.base import StorageConfigurationError
from ..storage.factory import redact_database_url, storage_health
from ..storage.rdbms import (
    SCHEMA_VERSION,
    EventRecord,
    PreviewRecord,
    SchemaVersionRecord,
    dispose_engines,
    get_engine,
    register_engine_lifecycle,
)


class StorageSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(dispose_engines)
        dispose_engines()

    def url(self, name: str) -> str:
        return f"sqlite:///{Path(self.tmp.name) / name}.db"

    def seed_legacy_schema(self, name: str, marker: str = "current") -> str:
        database_url = self.url(name)
        engine = create_engine(database_url)
        SQLModel.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(f'DROP TABLE "{SchemaVersionRecord.__tablename__}"')
            )
            if marker == "legacy":
                connection.execute(
                    text(
                        f'CREATE TABLE "{SchemaVersionRecord.__tablename__}" '
                        "(key VARCHAR(64) PRIMARY KEY, version INTEGER NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        f'INSERT INTO "{SchemaVersionRecord.__tablename__}" '
                        "(key, version) VALUES ('main', 1)"
                    )
                )
            else:
                connection.execute(
                    text(
                        f'CREATE TABLE "{SchemaVersionRecord.__tablename__}" '
                        "(name VARCHAR(64) PRIMARY KEY, version INTEGER NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        f'INSERT INTO "{SchemaVersionRecord.__tablename__}" '
                        "(name, version) VALUES ('audit', 1)"
                    )
                )
            connection.execute(
                text('DROP INDEX "ix_persistentlogger_previews_expires_at"')
            )
            connection.execute(
                text(
                    f'ALTER TABLE "{EventRecord.__tablename__}" DROP COLUMN "sequence"'
                )
            )
            connection.execute(
                text(
                    f'ALTER TABLE "{PreviewRecord.__tablename__}" '
                    'DROP COLUMN "expires_at"'
                )
            )
        engine.dispose()
        return database_url

    def test_new_database_has_a_version_marker_and_validated_layout(self):
        engine = get_engine(self.url("new"))
        with engine.connect() as connection:
            version = connection.execute(
                select(SchemaVersionRecord.version).where(
                    SchemaVersionRecord.name == "audit"
                )
            ).scalar_one()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn(str(EventRecord.__tablename__), inspect(engine).get_table_names())

    def test_upgrade_adds_missing_columns_and_updates_marker(self):
        database_url = self.seed_legacy_schema("upgrade")
        engine = get_engine(database_url)
        event_columns = {
            column["name"]
            for column in inspect(engine).get_columns(str(EventRecord.__tablename__))
        }
        preview_columns = {
            column["name"]
            for column in inspect(engine).get_columns(str(PreviewRecord.__tablename__))
        }
        self.assertIn("sequence", event_columns)
        self.assertIn("expires_at", preview_columns)
        with engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    select(SchemaVersionRecord.version).where(
                        SchemaVersionRecord.name == "audit"
                    )
                ).scalar_one(),
                SCHEMA_VERSION,
            )

    def test_upgrade_renames_the_real_legacy_marker(self):
        database_url = self.seed_legacy_schema("legacy-marker", marker="legacy")
        engine = get_engine(database_url)
        marker_columns = {
            column["name"]
            for column in inspect(engine).get_columns(
                str(SchemaVersionRecord.__tablename__)
            )
        }
        self.assertEqual(marker_columns, {"name", "version"})
        with engine.connect() as connection:
            row = connection.execute(
                select(SchemaVersionRecord.name, SchemaVersionRecord.version)
            ).one()
        self.assertEqual(row, ("audit", SCHEMA_VERSION))

    def test_future_schema_is_rejected_and_engine_is_not_cached(self):
        database_url = self.url("future")
        engine = create_engine(database_url)
        schema_table = getattr(SchemaVersionRecord, "__table__")
        schema_table.create(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    f'INSERT INTO "{SchemaVersionRecord.__tablename__}" '
                    "(name, version) VALUES ('audit', :version)"
                ),
                {"version": SCHEMA_VERSION + 1},
            )
        engine.dispose()
        with self.assertRaisesRegex(StorageConfigurationError, "incompatible.*schema"):
            get_engine(database_url)
        self.assertNotIn(database_url, rdbms._engines)

    def test_shutdown_hook_is_registered_once(self):
        was_registered = rdbms._lifecycle_registered
        rdbms._lifecycle_registered = False
        try:
            with mock.patch.object(rdbms.atexit, "register") as register:
                register_engine_lifecycle()
                register_engine_lifecycle()
            register.assert_called_once_with(dispose_engines)
        finally:
            rdbms._lifecycle_registered = was_registered

    def test_operational_storage_signals_redact_database_credentials(self):
        url = "postgresql+psycopg://logger:hidden-pass@localhost/audit"
        self.assertEqual(
            redact_database_url(url),
            "postgresql+psycopg://logger:***@localhost/audit",
        )
        health = storage_health(
            type("Settings", (), {"backend": "rdbms", "database_url": url})()
        )
        self.assertEqual(health["status"], "healthy")
        self.assertNotIn("hidden-pass", str(health))

    def test_profile_upgrade_does_not_overwrite_existing_registry_settings(self):
        upgrade_file = (
            Path(__file__).parents[1]
            / "profiles"
            / "default"
            / "upgrades"
            / "to_3"
            / "registry.xml"
        )
        upgrade_xml = upgrade_file.read_text()
        self.assertNotIn("<records", upgrade_xml)

    def test_storage_settings_cache_isolated_by_physical_site_path(self):
        first = type("Settings", (), {"backend": "zodb", "database_url": ""})()
        second = type(
            "Settings", (), {"backend": "rdbms", "database_url": "url"}
        )()
        first_registry = mock.MagicMock(forInterface=mock.MagicMock(return_value=first))
        second_registry = mock.MagicMock(
            forInterface=mock.MagicMock(return_value=second)
        )
        site_a = type("Site", (), {"getPhysicalPath": lambda self: ("", "a")})()
        site_b = type("Site", (), {"getPhysicalPath": lambda self: ("", "b")})()
        storage_factory.clear_cache()
        with (
            mock.patch.object(
                storage_factory, "getSite", side_effect=(site_a, site_b, site_a)
            ),
            mock.patch.object(
                storage_factory,
                "getUtility",
                side_effect=(first_registry, second_registry),
            ),
        ):
            self.assertIs(storage_factory.storage_settings(), first)
            self.assertIs(storage_factory.storage_settings(), second)
            self.assertIs(storage_factory.storage_settings(), first)
        self.assertEqual(first_registry.forInterface.call_count, 1)
        self.assertEqual(second_registry.forInterface.call_count, 1)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(StorageSchemaTests)
