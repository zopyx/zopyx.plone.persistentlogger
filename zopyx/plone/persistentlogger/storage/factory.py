"""Selection and construction of the audit log storage backend.

The site decides where audit records live through the *Audit log storage*
control panel (``zopyx.plone.persistentlogger.interfaces.IStorageSettings``).
This module is the single place that turns that configuration into a
repository, so every consumer -- the public API, the audit subscribers, the
retention service and the browser views -- automatically follows the setting.
"""

from __future__ import annotations

import os
import re
from typing import Any

from plone.registry.interfaces import IRegistry
from zope.component import ComponentLookupError, getUtility
from zope.component.hooks import getSite

from ..interfaces import IStorageSettings
from ..site_identity import stable_site_key
from .base import BaseLogStorage, StorageConfigurationError
from .zodb import AnnotationRepository

__all__ = [
    "BACKENDS",
    "BACKEND_RDBMS",
    "BACKEND_ZODB",
    "ENVIRONMENT_URL_VARIABLE",
    "build_rdbms_repository",
    "check_database_connection",
    "clear_cache",
    "get_repository",
    "redact_database_url",
    "resolve_backend",
    "resolve_database_url",
    "storage_settings",
    "storage_settings_changed",
    "storage_error_message",
    "storage_health",
    "validate_storage_configuration",
]

BACKEND_ZODB = "zodb"
BACKEND_RDBMS = "rdbms"
BACKENDS = (BACKEND_ZODB, BACKEND_RDBMS)

#: Environment fallback for the database URL, used when the control panel
#: field is left empty (containers, CI, environments without a site admin).
ENVIRONMENT_URL_VARIABLE = "ZOPYX_PERSISTENTLOGGER_DATABASE_URL"


class _FallbackSettings:
    """Settings used when the registry is not available yet."""

    backend = BACKEND_ZODB
    database_url = ""


# Registry lookups happen on every log write, so the proxy is cached per
# site and invalidated whenever a registry record changes.
_settings_cache: dict[tuple[str, ...], Any] = {}


def _site_key() -> tuple[str, ...]:
    """Return the stable key used for the current site's settings cache."""
    return stable_site_key(getSite())


def storage_settings() -> Any:
    """Return settings; use ZODB fallback only before a registry exists."""
    site_key = _site_key()
    cached = _settings_cache.get(site_key)
    if cached is not None:
        return cached
    try:
        registry = getUtility(IRegistry)
        settings = registry.forInterface(IStorageSettings, check=False)
    except ComponentLookupError:
        return _FallbackSettings()
    _settings_cache[site_key] = settings
    return settings


def storage_settings_changed(record: Any = None, event: Any = None) -> None:
    """Drop the cached settings when registry records change."""
    _settings_cache.clear()


def clear_cache() -> None:
    """Drop the cached settings (used by tests and setup code)."""
    _settings_cache.clear()


def resolve_backend(settings: Any = None) -> str:
    """Return the configured backend name."""
    settings = settings if settings is not None else storage_settings()
    backend = str(getattr(settings, "backend", "") or BACKEND_ZODB)
    if backend not in BACKENDS:
        raise StorageConfigurationError(
            f"unsupported audit log storage backend: {backend!r} "
            f"(expected one of {', '.join(BACKENDS)})"
        )
    return backend


def resolve_database_url(settings: Any = None) -> str:
    """Return the configured database URL or an empty string.

    The control panel value wins; when it is empty the environment variable
    ``ZOPYX_PERSISTENTLOGGER_DATABASE_URL`` is used as a fallback.
    """
    settings = settings if settings is not None else storage_settings()
    url = str(getattr(settings, "database_url", "") or "").strip()
    if url:
        return url
    return os.environ.get(ENVIRONMENT_URL_VARIABLE, "").strip()


_AUTHORITY_SECRET = re.compile(r"(://[^/:@\s]+:)[^/@\s]+(@)")
_QUERY_SECRET = re.compile(
    r"([?&](?:password|passwd|pwd|secret|token|api[_-]?key|key)=)[^&\s]*",
    re.IGNORECASE,
)


def redact_database_url(database_url: str) -> str:
    """Return a display-safe database URL without credentials."""
    value = str(database_url or "")
    try:
        from sqlalchemy.engine import make_url

        value = make_url(value).render_as_string(hide_password=True)
    except Exception:
        value = _AUTHORITY_SECRET.sub(r"\1***\2", value)
    return _QUERY_SECRET.sub(r"\1***", value)


def storage_error_message(error: Exception, database_url: str = "") -> str:
    """Return an operational error with connection credentials removed."""
    message = str(error)
    if database_url:
        message = message.replace(database_url, redact_database_url(database_url))
    message = _AUTHORITY_SECRET.sub(r"\1***\2", message)
    return _QUERY_SECRET.sub(r"\1***", message)


def storage_health(settings: Any = None, check: bool = False) -> dict[str, Any]:
    """Return a safe, machine-readable storage configuration status."""
    try:
        backend = resolve_backend(settings)
        url = resolve_database_url(settings)
        error = validate_storage_configuration(backend, url)
        if error is not None:
            return {
                "status": "unhealthy",
                "code": "invalid_configuration",
                "backend": backend,
                "database_url": redact_database_url(url),
                "error": error,
            }
        if check and backend == BACKEND_RDBMS:
            error = check_database_connection(url)
            if error is not None:
                return {
                    "status": "unhealthy",
                    "code": "database_unavailable",
                    "backend": backend,
                    "database_url": redact_database_url(url),
                    "error": error,
                }
        return {
            "status": "healthy",
            "code": "ok",
            "backend": backend,
            "database_url": redact_database_url(url),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "code": "storage_configuration_error",
            "backend": None,
            "database_url": "",
            "error": storage_error_message(exc),
        }


def build_rdbms_repository(context: Any, database_url: str) -> BaseLogStorage:
    try:
        from .rdbms import SQLRepository
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise StorageConfigurationError(
            "the RDBMS audit log storage backend requires the optional 'rdbms' "
            f"extra (SQLModel, SQLAlchemy and a database driver): {exc}"
        ) from exc
    return SQLRepository(context, database_url=database_url)


def get_repository(
    context: Any,
    *,
    backend: str | None = None,
    database_url: str | None = None,
    settings: Any = None,
) -> BaseLogStorage:
    """Return the repository configured for ``context``.

    ``backend`` and ``database_url`` override the site configuration and
    exist for tests and migrations.
    """
    if backend is None:
        backend = resolve_backend(settings)
    if backend == BACKEND_ZODB:
        return AnnotationRepository(context)
    if backend == BACKEND_RDBMS:
        url = (
            database_url if database_url is not None else resolve_database_url(settings)
        )
        if not url:
            raise StorageConfigurationError(
                "the RDBMS audit log storage backend needs a database URL: set it "
                "in the 'Audit log storage' control panel or in the "
                f"{ENVIRONMENT_URL_VARIABLE} environment variable"
            )
        return build_rdbms_repository(context, url)
    raise StorageConfigurationError(
        f"unsupported audit log storage backend: {backend!r} "
        f"(expected one of {', '.join(BACKENDS)})"
    )


def _parse_database_url(database_url: str) -> Any:
    try:
        from sqlalchemy.engine import make_url
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise StorageConfigurationError(
            "validating a database URL requires the optional 'rdbms' extra "
            f"(SQLModel/SQLAlchemy): {exc}"
        ) from exc
    return make_url(database_url)


def validate_storage_configuration(
    backend: str | None, database_url: str | None
) -> str | None:
    """Return an error message for an unusable storage configuration.

    Used by the control panel to refuse saving a configuration that cannot
    work, instead of failing later on the first log write.
    """
    try:
        backend = str(backend or BACKEND_ZODB)
        if backend not in BACKENDS:
            return (
                f"Unsupported audit log storage backend: {backend!r}. "
                f"Choose one of {', '.join(BACKENDS)}."
            )
        if backend == BACKEND_ZODB:
            return None
        url = (database_url or "").strip()
        if not url:
            return (
                "A database URL is required for the RDBMS backend, for example "
                "postgresql+psycopg://user:password@host:5432/database."
            )
        _parse_database_url(url)
    except StorageConfigurationError as exc:
        return str(exc)
    except Exception as exc:
        detail = storage_error_message(exc, database_url or "")
        return f"The database URL is not valid: {detail}"
    return None


def check_database_connection(database_url: str) -> str | None:
    """Return an error message when the database cannot be reached."""
    try:
        from .rdbms import check_connection
    except ImportError as exc:  # pragma: no cover - depends on the environment
        return (
            "Connecting to a database requires the optional 'rdbms' extra "
            f"(SQLModel, SQLAlchemy and a database driver): {exc}"
        )
    try:
        check_connection(database_url)
    except Exception as exc:
        safe_url = redact_database_url(database_url)
        detail = storage_error_message(exc, database_url)
        return f"Could not connect to the configured database {safe_url}: {detail}"
    return None
