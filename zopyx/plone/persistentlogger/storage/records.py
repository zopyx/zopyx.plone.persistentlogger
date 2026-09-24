"""Accessors for the canonical record dictionaries.

Kept in a module of its own so that both the storage contract
(:mod:`zopyx.plone.persistentlogger.storage.base`) and the query model
(:mod:`zopyx.plone.persistentlogger.storage.query`) can use them without
importing each other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

__all__ = ["event_date", "event_id_of", "severity_value"]


def event_id_of(entry: dict[str, Any]) -> str:
    """Return the record identifier of an event entry."""
    return str(entry.get("uuid", entry.get("event_id", "")))


def event_date(entry: dict[str, Any]) -> datetime:
    """Return the (UTC) timestamp of an event entry."""
    value = entry.get("date", entry.get("created_at"))
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def severity_value(entry: dict[str, Any]) -> str:
    """Return the severity of an entry as a plain string."""
    value = entry.get("severity", entry.get("level", "info"))
    return str(getattr(value, "value", value))
