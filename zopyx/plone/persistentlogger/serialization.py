"""Serialization helpers and integrity hashes for governance records."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return sorted(value, key=repr)
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def event_row(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return {
            "event_id": str(event.get("uuid", event.get("event_id", ""))),
            "created_at": event.get("date", event.get("created_at")),
            "actor": event.get("username", event.get("actor", "")),
            "event_type": event.get("event_type", "application"),
            "severity": event.get("level", event.get("severity", "info")),
            "target": event.get("target", ""),
            "comment": event.get("comment", ""),
            "info_url": event.get("info_url"),
            "details": event.get("details_raw", event.get("details")),
            "schema_version": event.get("schema_version", 0),
            "integrity_digest": event.get("integrity_digest"),
        }
    return {
        "event_id": str(event.event_id),
        "created_at": event.created_at,
        "actor": event.actor,
        "event_type": event.event_type,
        "severity": event.severity,
        "target": event.target,
        "comment": event.comment,
        "info_url": event.info_url,
        "details": event.details,
        "schema_version": event.schema_version,
        "integrity_digest": event.integrity_digest,
    }


def event_digest(event: Any, previous_digest: str = "") -> str:
    import hashlib

    payload = {"previous_digest": previous_digest, "event": event_row(event)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def export_rows(events: list[Any]) -> list[dict[str, Any]]:
    return [event_row(event) for event in events]
