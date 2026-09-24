"""Backwards compatible import location for the ZODB annotation repository.

The storage layer moved to :mod:`zopyx.plone.persistentlogger.storage`, which
holds one module per backend plus the factory that picks the configured one.
This module keeps the historical import paths working for third party code.
Prefer ``from zopyx.plone.persistentlogger.storage import get_repository`` in
new code, which honours the control panel setting.
"""

from __future__ import annotations

from .storage.base import (
    event_date,
    event_id_of,
    object_uid,
    selection_digest,
)
from .storage.base import event_date as _event_date
from .storage.zodb import (
    JOURNAL_KEY,
    LOG_KEY,
    POLICY_KEY,
    PREVIEW_KEY,
    AnnotationRepository,
)

__all__ = [
    "JOURNAL_KEY",
    "LOG_KEY",
    "POLICY_KEY",
    "PREVIEW_KEY",
    "AnnotationRepository",
    "_event_date",
    "_event_id",
    "event_date",
    "event_id_of",
    "object_uid",
    "selection_digest",
]

#: Historical alias of :func:`event_id_of`.
_event_id = event_id_of
