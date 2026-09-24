"""Unit tests for the storage backend selection and validation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ..storage import factory
from ..storage.base import StorageConfigurationError
from ..storage.factory import (
    BACKEND_RDBMS,
    BACKEND_ZODB,
    BACKENDS,
    ENVIRONMENT_URL_VARIABLE,
    check_database_connection,
    get_repository,
    resolve_backend,
    resolve_database_url,
    storage_settings,
    storage_settings_changed,
    validate_storage_configuration,
)
from ..storage.zodb import AnnotationRepository

POSTGRES_URL = "postgresql+psycopg://logger:secret@localhost:5432/audit"


def settings(backend=BACKEND_ZODB, database_url=""):
    return MagicMock(backend=backend, database_url=database_url)


class ResolveSettingsTests(unittest.TestCase):
    def setUp(self):
        factory.clear_cache()
        self.addCleanup(factory.clear_cache)
        self.environment = patch.dict(
            "os.environ", {ENVIRONMENT_URL_VARIABLE: ""}, clear=False
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_backends_are_zodb_and_rdbms(self):
        self.assertEqual(BACKENDS, (BACKEND_ZODB, BACKEND_RDBMS))

    def test_default_backend_is_zodb(self):
        self.assertEqual(resolve_backend(settings(backend="")), BACKEND_ZODB)

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(StorageConfigurationError):
            resolve_backend(settings(backend="sqlite"))

    def test_database_url_prefers_the_configuration(self):
        self.assertEqual(
            resolve_database_url(settings(database_url=POSTGRES_URL)), POSTGRES_URL
        )
        self.assertEqual(
            resolve_database_url(settings(database_url="  " + POSTGRES_URL + " ")),
            POSTGRES_URL,
        )

    def test_database_url_falls_back_to_the_environment(self):
        with patch.dict("os.environ", {ENVIRONMENT_URL_VARIABLE: POSTGRES_URL}):
            self.assertEqual(resolve_database_url(settings()), POSTGRES_URL)
        self.assertEqual(resolve_database_url(settings()), "")

    def test_fallback_settings_are_used_without_a_registry(self):
        with patch.object(factory, "getUtility", side_effect=Exception("no registry")):
            current = storage_settings()
        self.assertEqual(current.backend, BACKEND_ZODB)
        self.assertEqual(current.database_url, "")

    def test_settings_are_cached_per_site_and_invalidated(self):
        registry = MagicMock(
            forInterface=MagicMock(return_value=settings(backend=BACKEND_ZODB))
        )
        with patch.object(factory, "getUtility", return_value=registry):
            first = storage_settings()
            second = storage_settings()
            self.assertIs(first, second)
            self.assertEqual(registry.forInterface.call_count, 1)
            storage_settings_changed()
            storage_settings()
            self.assertEqual(registry.forInterface.call_count, 2)

    def test_get_repository_returns_the_zodb_backend_for_zodb(self):
        repository = get_repository(object(), settings=settings())
        self.assertIsInstance(repository, AnnotationRepository)

    def test_get_repository_builds_the_rdbms_backend(self):
        built = MagicMock()
        with patch.object(factory, "build_rdbms_repository", return_value=built):
            repository = get_repository(
                object(), settings=settings(BACKEND_RDBMS, POSTGRES_URL)
            )
        self.assertIs(repository, built)

    def test_get_repository_requires_a_url_for_rdbms(self):
        with self.assertRaises(StorageConfigurationError) as error:
            get_repository(object(), settings=settings(BACKEND_RDBMS, ""))
        self.assertIn(ENVIRONMENT_URL_VARIABLE, str(error.exception))

    def test_get_repository_accepts_explicit_overrides(self):
        repository = get_repository(object(), backend="zodb", database_url="")
        self.assertIsInstance(repository, AnnotationRepository)
        with patch.object(factory, "build_rdbms_repository", return_value="built"):
            self.assertEqual(
                get_repository(
                    object(), backend=BACKEND_RDBMS, database_url=POSTGRES_URL
                ),
                "built",
            )

    def test_get_repository_rejects_unknown_backends(self):
        with self.assertRaises(StorageConfigurationError):
            get_repository(object(), backend="mysql")

    def test_missing_rdbms_extra_reports_a_clear_error(self):
        with (
            patch.dict(
                "sys.modules", {"zopyx.plone.persistentlogger.storage.rdbms": None}
            ),
            self.assertRaises(StorageConfigurationError) as error,
        ):
            factory.build_rdbms_repository(object(), POSTGRES_URL)
        self.assertIn("rdbms", str(error.exception))


class ValidateStorageConfigurationTests(unittest.TestCase):
    def test_zodb_configuration_needs_no_url(self):
        self.assertIsNone(validate_storage_configuration(BACKEND_ZODB, ""))
        self.assertIsNone(validate_storage_configuration(BACKEND_ZODB, POSTGRES_URL))

    def test_unknown_backend_is_reported(self):
        message = validate_storage_configuration("oracle", POSTGRES_URL)
        self.assertIn("Unsupported audit log storage backend", message)

    def test_rdbms_requires_a_url(self):
        for value in ("", None, "   "):
            message = validate_storage_configuration(BACKEND_RDBMS, value)
            self.assertIn("database URL is required", message)

    def test_unparsable_url_is_reported(self):
        message = validate_storage_configuration(BACKEND_RDBMS, "not a url")
        self.assertIn("not valid", message)

    def test_valid_url_is_accepted(self):
        self.assertIsNone(validate_storage_configuration(BACKEND_RDBMS, POSTGRES_URL))

    def test_missing_sqlmodel_extra_is_reported(self):
        with patch.object(
            factory,
            "_parse_database_url",
            side_effect=StorageConfigurationError("install the rdbms extra"),
        ):
            self.assertEqual(
                validate_storage_configuration(BACKEND_RDBMS, POSTGRES_URL),
                "install the rdbms extra",
            )


class CheckDatabaseConnectionTests(unittest.TestCase):
    def test_reachable_database_returns_none(self):
        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.check_connection"
        ) as check:
            self.assertIsNone(check_database_connection(POSTGRES_URL))
        check.assert_called_once_with(POSTGRES_URL)

    def test_unreachable_database_is_reported(self):
        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.check_connection",
            side_effect=OSError("connection refused"),
        ):
            message = check_database_connection(POSTGRES_URL)
        self.assertIn("Could not connect", message)
        self.assertIn("connection refused", message)

    def test_missing_rdbms_extra_is_reported(self):
        with patch.dict(
            "sys.modules", {"zopyx.plone.persistentlogger.storage.rdbms": None}
        ):
            message = check_database_connection(POSTGRES_URL)
        self.assertIn("rdbms", message)


def test_suite():
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(ResolveSettingsTests))
    suite.addTest(loader.loadTestsFromTestCase(ValidateStorageConfigurationTests))
    suite.addTest(loader.loadTestsFromTestCase(CheckDatabaseConnectionTests))
    return suite
