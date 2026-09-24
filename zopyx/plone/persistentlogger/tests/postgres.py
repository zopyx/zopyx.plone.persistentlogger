"""Shared PostgreSQL test container used by the RDBMS test suites.

The container is started on first use and shared by every test class of the
process, which keeps the tests fast while still exercising a real PostgreSQL
server.  When Docker (or the container runtime) is unavailable the tests are
skipped with an explanatory message instead of failing.
"""

from __future__ import annotations

import atexit
import os
import unittest

__all__ = ["database_url", "require_postgres", "stop_container"]

IMAGE = "postgres:17-alpine"

#: Set to a non-empty value to turn "no container runtime" into a hard error
#: instead of a skip.  ``make test`` sets it, so the canonical test run never
#: silently drops the RDBMS half of the suite.
REQUIRE_VARIABLE = "ZOPYX_PERSISTENTLOGGER_REQUIRE_POSTGRES"

_container = None
_url: str | None = None
_skip_reason: str | None = None


class PostgresUnavailable(RuntimeError):
    """Raised when the PostgreSQL test container cannot be started."""


def require_postgres() -> bool:
    """Return whether a missing container runtime must fail the test run."""
    return bool(os.environ.get(REQUIRE_VARIABLE, "").strip())


def _postgres_container(image: str):
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - depends on the installed version
        from testcontainers.postgres import PostgresContainer
    return PostgresContainer(image, driver="psycopg")


def database_url() -> str:
    """Return the connection URL of the shared container.

    Raises :class:`unittest.SkipTest` when the container cannot be started and
    :class:`PostgresUnavailable` when ``REQUIRE_VARIABLE`` is set -- the
    canonical ``make test`` run must not silently drop the RDBMS tests.
    """
    global _container, _url, _skip_reason
    if _url is not None:
        return _url
    if _skip_reason is not None:
        raise _unavailable(_skip_reason)

    container = None
    try:
        container = _postgres_container(IMAGE)
        _container = container
        container.start()
        url = str(container.get_connection_url())
    except Exception as exc:  # noqa: BLE001 - any failure means "no container"
        if container is not None:
            try:
                container.stop()
            except Exception:  # noqa: BLE001 - preserve the original failure
                pass
        _container = None
        _url = None
        _skip_reason = (
            "PostgreSQL test container is unavailable "
            f"(is a container runtime running?): {exc}"
        )
        raise _unavailable(_skip_reason) from exc

    _url = url
    atexit.register(stop_container)
    return _url


def _unavailable(reason: str) -> Exception:
    """Return the exception to raise for an unavailable container."""
    if require_postgres():
        return PostgresUnavailable(
            f"{reason}\nThe {REQUIRE_VARIABLE} variable is set, so the full "
            "suite (ZODB and PostgreSQL) is required. Start Docker and re-run "
            "`make test`, or run `make test-zodb` for the ZODB half only."
        )
    return unittest.SkipTest(reason)


def stop_container() -> None:
    """Stop the container and dispose the cached engines."""
    global _container, _url
    from zopyx.plone.persistentlogger.storage.rdbms import dispose_engines

    container = _container
    try:
        dispose_engines()
    finally:
        try:
            if container is not None:
                container.stop()
        finally:
            _container = None
            _url = None
