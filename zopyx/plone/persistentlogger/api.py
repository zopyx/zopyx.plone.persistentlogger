"""Stable application API for logging, retention, and exports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import plone.api

from .exports import export_events
from .models import DeletionPreview, DeletionResult, LogEvent, RetentionPolicy
from .repository import AnnotationRepository
from .retention import RetentionService


def log_event(
    context: Any, comment: str, *, level: str = "info", **kwargs: Any
) -> dict[str, Any]:
    """Append one event to the context's persistent log."""
    event = LogEvent(
        comment=comment,
        severity=level,
        actor=kwargs.pop("actor", plone.api.user.get_current().getUserName()),
        **kwargs,
    )
    return AnnotationRepository(context).append(event)


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


def export_log(context: Any, format: str, **kwargs: Any) -> bytes:
    """Export one object's log in a supported format."""
    return export_events(AnnotationRepository(context).events(), format, **kwargs)
