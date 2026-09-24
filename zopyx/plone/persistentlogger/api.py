"""Stable application API for logging, retention, and exports.

Every function resolves its repository through
:func:`zopyx.plone.persistentlogger.storage.get_repository`, so callers do not
need to know whether records are stored in the ZODB or in an RDBMS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import plone.api

from .exports import export_events
from .models import (
    DeletionPreview,
    DeletionResult,
    ExportRequest,
    LogEvent,
    RetentionPolicy,
)
from .retention import RetentionService
from .storage import get_repository


def log_event(
    context: Any, comment: str, *, level: str = "info", **kwargs: Any
) -> dict[str, Any]:
    """Append one event to the configured persistent log of ``context``."""
    event = LogEvent(
        comment=comment,
        severity=level,
        actor=kwargs.pop("actor", plone.api.user.get_current().getUserName()),
        **kwargs,
    )
    return get_repository(context).append(event)


def preview_retention(
    context: Any, policy: RetentionPolicy, now: datetime | None = None
) -> DeletionPreview:
    """Create a server-side deletion preview for one object."""
    return RetentionService(context).preview(policy, now)


def execute_retention(
    context: Any, preview: DeletionPreview, reason: str, actor: str
) -> DeletionResult:
    """Execute a previously generated deletion preview."""
    return RetentionService(context).execute(preview, reason, actor)


def export_log(context: Any, format: str) -> bytes:
    """Export one object's bounded log in a supported format."""
    request = ExportRequest(format)
    repository = get_repository(context)
    result = repository.search(limit=request.max_entries + 1)
    if result.total > request.max_entries:
        raise ValueError("export exceeds the configured entry limit")
    return export_events(
        list(result.rows),
        request.format,
        max_entries=request.max_entries,
        max_bytes=request.max_bytes,
    )
