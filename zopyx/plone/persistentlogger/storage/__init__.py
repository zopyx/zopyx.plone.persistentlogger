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
    event_digest,
    event_id_of,
    governance_digest,
    object_uid,
    selection_digest,
    severity_value,
    verify_event_chain,
    verify_governance_chain,
)
from .contracts import LogRepository
from .factory import (
    BACKEND_RDBMS,
    BACKEND_ZODB,
    BACKENDS,
    check_database_connection,
    get_repository,
    redact_database_url,
    resolve_backend,
    resolve_database_url,
    storage_error_message,
    storage_health,
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
    CHAIN_HEAD_KEY,
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
    "CHAIN_HEAD_KEY",
    "JOURNAL_KEY",
    "LOG_KEY",
    "POLICY_KEY",
    "PREVIEW_KEY",
    "AnnotationRepository",
    "BaseLogStorage",
    "LogRepository",
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
    "event_digest",
    "event_id_of",
    "get_repository",
    "redact_database_url",
    "governance_digest",
    "object_uid",
    "resolve_backend",
    "resolve_database_url",
    "selection_digest",
    "severity_value",
    "storage_settings",
    "storage_settings_changed",
    "storage_error_message",
    "storage_health",
    "validate_storage_configuration",
    "verify_event_chain",
    "verify_governance_chain",
]
