"""Idempotent migration of legacy annotation log records."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..serialization import event_digest


def migrate_store(store: Any) -> int:
    """Normalize legacy entries in-place and return the number changed."""
    changed = 0
    entries = list(store.items())
    entries.sort(
        key=lambda item: (
            item[1].get("date", item[0]) if isinstance(item[1], dict) else item[0]
        )
    )
    previous = ""
    for key, value in entries:
        if not isinstance(value, dict):
            continue
        event_id = str(value.get("uuid") or value.get("event_id") or uuid4())
        original = dict(value)
        value.setdefault("uuid", event_id)
        value.setdefault("event_id", event_id)
        value.setdefault("created_at", value.get("date"))
        value.setdefault("actor", value.get("username", ""))
        value.setdefault("event_type", "application")
        value.setdefault("severity", value.get("level", "info"))
        value.setdefault("schema_version", 1)
        value.setdefault("previous_digest", previous)
        value.setdefault("integrity_digest", event_digest(value, previous))
        previous = str(value["integrity_digest"])
        if key != event_id or value != original:
            changed += 1
        if key != event_id:
            del store[key]
            store[event_id] = value
    if changed:
        store._p_changed = True
    return changed
