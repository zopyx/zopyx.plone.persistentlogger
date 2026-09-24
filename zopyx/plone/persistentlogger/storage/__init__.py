"""Storage layer of the persistent audit logger.

Two interchangeable backends implement one contract defined in
:mod:`~zopyx.plone.persistentlogger.storage.base`:

``zodb``
    Records live in the annotations of the logged object (the historical
    behaviour, transactional with the object).
``rdbms``
    Records live in a relational database, modelled with SQLModel and
    reached through SQLAlchemy.

Use :func:`get_repository` to obtain the repository configured for the
current site instead of instantiating a backend directly.
"""

from __future__ import annotations

from .base import (
    BaseLogStorage,
    StorageConfigurationError,
    event_date,
    event_id_of,
    object_uid,
    selection_digest,
    severity_value,
)
from .factory import (
    BACKEND_RDBMS,
    BACKEND_ZODB,
    BACKENDS,
    check_database_connection,
    get_repository,
    resolve_backend,
    resolve_database_url,
    storage_settings,
    storage_settings_changed,
    validate_storage_configuration,
)
from .query import (
    COLUMNS,
    Column,
    Condition,
    ConditionGroup,
    QueryError,
    SearchResult,
    SortSpec,
    columns_json,
    default_sort,
    parse_filter_model,
    parse_sort_model,
)
from .zodb import (
    JOURNAL_KEY,
    LOG_KEY,
    POLICY_KEY,
    PREVIEW_KEY,
    AnnotationRepository,
)

__all__ = [
    "BACKENDS",
    "BACKEND_RDBMS",
    "BACKEND_ZODB",
    "JOURNAL_KEY",
    "LOG_KEY",
    "POLICY_KEY",
    "PREVIEW_KEY",
    "AnnotationRepository",
    "BaseLogStorage",
    "COLUMNS",
    "Column",
    "Condition",
    "ConditionGroup",
    "QueryError",
    "SearchResult",
    "SortSpec",
    "StorageConfigurationError",
    "columns_json",
    "default_sort",
    "parse_filter_model",
    "parse_sort_model",
    "check_database_connection",
    "event_date",
    "event_id_of",
    "get_repository",
    "object_uid",
    "resolve_backend",
    "resolve_database_url",
    "selection_digest",
    "severity_value",
    "storage_settings",
    "storage_settings_changed",
    "validate_storage_configuration",
]
