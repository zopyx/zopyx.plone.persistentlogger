"""Serialization helpers and integrity hashes for governance records."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "client_secret",
        "cookie",
        "credential",
        "cvc",
        "cvv",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "ssn",
        "token",
    }
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_sensitive(value: Any) -> Any:
    """Recursively redact secret fields in a JSON-like payload.

    Redaction happens before validation and before a payload reaches a storage
    or export boundary. Keys are matched case-insensitively, including nested
    mappings and sequences.
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [redact_sensitive(item) for item in sorted(value, key=repr)]
    return value


def normalize_details(value: Any, *, redact: bool = True) -> Any:
    """Normalize a value to the package's JSON-compatible subset.

    Public storage/export values are redacted.  Integrity verification uses
    ``redact=False`` so a changed value already present in a stored record is
    not hidden by applying redaction a second time.
    """
    if redact:
        value = redact_sensitive(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("details must contain finite numbers")
        return value
    if isinstance(value, (datetime, date, UUID, Enum)):
        return json_default(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("details mapping keys must be strings")
            normalized[key] = normalize_details(item, redact=redact)
        return normalized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_details(item, redact=redact) for item in value]
    raise ValueError(
        f"details must contain only JSON-compatible values (got {type(value).__name__})"
    )


def sanitized_details(value: Any) -> Any:
    """Return a validated, redacted payload suitable for storage/export."""
    normalized = normalize_details(value)
    try:
        encoded = canonical_json(normalized).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("details must contain JSON-compatible values") from exc
    if len(encoded) > 65536:
        raise ValueError("details must not exceed 64 KiB")
    return normalized


def integrity_details(value: Any) -> Any:
    """Normalize stored details without redacting their current values."""
    return normalize_details(value, redact=False)


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


def canonical_json(value: Any, *, redact: bool = True) -> str:
    """Serialize deterministically, redacting at public boundaries by default."""
    return json.dumps(
        redact_sensitive(value) if redact else value,
        default=json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def event_row(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        row = {
            "event_id": str(event.get("uuid", event.get("event_id", ""))),
            "created_at": event.get("date", event.get("created_at")),
            "actor": event.get("username", event.get("actor", "")),
            "event_type": event.get("event_type", "application"),
            "severity": event.get("level", event.get("severity", "info")),
            "target": event.get("target", ""),
            "comment": event.get("comment", ""),
            "info_url": event.get("info_url"),
            "details": sanitized_details(
                event.get("details_raw", event.get("details"))
            ),
            "schema_version": event.get("schema_version", 0),
            "integrity_digest": event.get("integrity_digest"),
        }
        return row
    return {
        "event_id": str(event.event_id),
        "created_at": event.created_at,
        "actor": event.actor,
        "event_type": event.event_type,
        "severity": getattr(event.severity, "value", event.severity),
        "target": event.target,
        "comment": event.comment,
        "info_url": event.info_url,
        "details": sanitized_details(event.details),
        "schema_version": event.schema_version,
        "integrity_digest": event.integrity_digest,
    }


def _canonical_datetime(value: Any) -> datetime:
    """Normalize an event timestamp for hashing and migration ordering."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        return _canonical_datetime(parsed)
    return datetime.min.replace(tzinfo=UTC)


def canonical_event_date(event: Any) -> datetime:
    """Return a legacy-compatible event date normalized to UTC."""
    if isinstance(event, dict):
        values = (event.get("created_at"), event.get("date"))
    else:
        values = (getattr(event, "created_at", None),)
    for value in values:
        if isinstance(value, (datetime, date)):
            return _canonical_datetime(value)
        if isinstance(value, str):
            try:
                return _canonical_datetime(
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                )
            except ValueError:
                continue
    return datetime.min.replace(tzinfo=UTC)


def canonical_event_payload(
    event: Any, previous_digest: str | None = None
) -> dict[str, Any]:
    """Return the one event payload used by storage and migrations.

    Legacy aliases are accepted on input, but never participate in the hash.
    The stored digest is deliberately excluded, making the result suitable for
    both creating and verifying records.
    """
    if isinstance(event, dict):
        value = event
        event_id = value.get("event_id", value.get("uuid", ""))
        created_at = value.get("created_at", value.get("date"))
        actor = value.get("actor", value.get("username", ""))
        event_type = value.get("event_type", "application")
        severity = value.get("severity", value.get("level", "info"))
        target = value.get("target", "")
        comment = value.get("comment", "")
        info_url = value.get("info_url")
        details = value.get("details", value.get("details_raw"))
        schema_version = value.get("schema_version", 1)
        previous = (
            previous_digest
            if previous_digest is not None
            else value.get("previous_digest", "")
        )
    else:
        value = None
        event_id = event.event_id
        created_at = event.created_at
        actor = event.actor
        event_type = event.event_type
        severity = event.severity
        target = event.target
        comment = event.comment
        info_url = event.info_url
        details = event.details
        schema_version = event.schema_version
        previous = previous_digest if previous_digest is not None else ""

    try:
        normalized_schema_version = int(schema_version or 1)
    except (TypeError, ValueError):
        normalized_schema_version = 1
    return {
        "event_id": str(event_id),
        "created_at": _canonical_datetime(created_at),
        "actor": str(actor or ""),
        "event_type": str(event_type or ""),
        "severity": str(getattr(severity, "value", severity) or ""),
        "target": str(target or ""),
        "comment": str(comment or ""),
        "info_url": None if info_url is None else str(info_url),
        "details": integrity_details(details),
        "schema_version": normalized_schema_version,
        "previous_digest": str(previous or ""),
        **(
            {"sequence": int(value["sequence"])}
            if value is not None and value.get("sequence") is not None
            else {}
        ),
    }


def event_digest(event: Any, previous_digest: str | None = None) -> str:
    """Return the canonical, non-self-referential event digest."""
    import hashlib

    payload = canonical_event_payload(event, previous_digest)
    return hashlib.sha256(
        canonical_json(payload, redact=False).encode("utf-8")
    ).hexdigest()


def export_rows(events: list[Any]) -> list[dict[str, Any]]:
    return [event_row(event) for event in events]
