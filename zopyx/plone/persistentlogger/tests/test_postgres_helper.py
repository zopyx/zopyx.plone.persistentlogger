"""Unit tests for the PostgreSQL test container helper.

The helper decides whether the RDBMS half of the suite runs, is skipped or --
in the canonical ``make test`` run -- fails the whole run.  These tests cover
those decisions without starting a real container.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from . import postgres
from .postgres import (
    PostgresUnavailable,
    database_url,
    require_postgres,
    stop_container,
)


class PostgresContainerHelperTests(unittest.TestCase):
    """Behaviour of ``tests/postgres.py`` outside a running container."""

    def setUp(self):
        # Never touch the container the other test modules share.
        self.saved = (postgres._container, postgres._url, postgres._skip_reason)
        postgres._container = None
        postgres._url = None
        postgres._skip_reason = None
        self.addCleanup(self._restore)

    def _restore(self):
        postgres._container, postgres._url, postgres._skip_reason = self.saved

    def test_require_postgres_follows_the_environment(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(postgres.REQUIRE_VARIABLE, None)
            self.assertFalse(require_postgres())
            os.environ[postgres.REQUIRE_VARIABLE] = "1"
            self.assertTrue(require_postgres())
            os.environ[postgres.REQUIRE_VARIABLE] = "   "
            self.assertFalse(require_postgres())

    def test_missing_container_runtime_skips_the_tests(self):
        with (
            patch.dict(os.environ, {postgres.REQUIRE_VARIABLE: ""}),
            patch.object(
                postgres, "_postgres_container", side_effect=RuntimeError("no runtime")
            ),
        ):
            with self.assertRaises(unittest.SkipTest) as caught:
                database_url()
        self.assertIn("no runtime", str(caught.exception))
        self.assertIn("container runtime", str(caught.exception))

    def test_missing_container_runtime_fails_when_required(self):
        with patch.object(
            postgres, "_postgres_container", side_effect=RuntimeError("no runtime")
        ):
            with patch.dict(os.environ, {postgres.REQUIRE_VARIABLE: "1"}):
                with self.assertRaises(PostgresUnavailable) as caught:
                    database_url()
        message = str(caught.exception)
        self.assertIn("no runtime", message)
        self.assertIn(postgres.REQUIRE_VARIABLE, message)
        self.assertIn("make test", message)

    def test_a_failed_start_is_remembered(self):
        with (
            patch.dict(os.environ, {postgres.REQUIRE_VARIABLE: ""}),
            patch.object(
                postgres, "_postgres_container", side_effect=RuntimeError("no runtime")
            ),
        ):
            with self.assertRaises(unittest.SkipTest):
                database_url()
            # the second call fails fast instead of starting another container
            with self.assertRaises(unittest.SkipTest):
                database_url()

    def test_a_container_that_fails_to_start_is_stopped(self):
        container = MagicMock()
        container.start.side_effect = RuntimeError("start failed")
        with (
            patch.dict(os.environ, {postgres.REQUIRE_VARIABLE: ""}),
            patch.object(postgres, "_postgres_container", return_value=container),
        ):
            with self.assertRaises(unittest.SkipTest):
                database_url()
        container.stop.assert_called_once_with()
        self.assertIsNone(postgres._container)

    def test_a_container_without_a_url_is_stopped(self):
        container = MagicMock()
        container.get_connection_url.side_effect = RuntimeError("url failed")
        with (
            patch.dict(os.environ, {postgres.REQUIRE_VARIABLE: ""}),
            patch.object(postgres, "_postgres_container", return_value=container),
        ):
            with self.assertRaises(unittest.SkipTest):
                database_url()
        container.stop.assert_called_once_with()
        self.assertIsNone(postgres._container)

    def test_the_shared_container_is_started_once(self):
        container = MagicMock()
        container.get_connection_url.return_value = "postgresql+psycopg://shared"
        with patch.object(
            postgres, "_postgres_container", return_value=container
        ) as factory:
            self.assertEqual(database_url(), "postgresql+psycopg://shared")
            self.assertEqual(database_url(), "postgresql+psycopg://shared")
        factory.assert_called_once_with(postgres.IMAGE)
        container.start.assert_called_once()

    def test_stop_container_disposes_the_engines_and_stops_the_container(self):
        container = MagicMock()
        postgres._container = container
        postgres._url = "postgresql+psycopg://shared"
        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.dispose_engines"
        ) as dispose:
            stop_container()
        dispose.assert_called_once_with()
        container.stop.assert_called_once_with()
        self.assertIsNone(postgres._container)
        self.assertIsNone(postgres._url)

    def test_stop_container_stops_after_engine_disposal_fails(self):
        container = MagicMock()
        postgres._container = container
        postgres._url = "postgresql+psycopg://shared"
        with patch(
            "zopyx.plone.persistentlogger.storage.rdbms.dispose_engines",
            side_effect=RuntimeError("dispose failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "dispose failed"):
                stop_container()
        container.stop.assert_called_once_with()
        self.assertIsNone(postgres._container)
        self.assertIsNone(postgres._url)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        PostgresContainerHelperTests
    )
