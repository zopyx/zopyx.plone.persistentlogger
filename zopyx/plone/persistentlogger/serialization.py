"""Serialization helpers and integrity hashes for governance records."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
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


def normalize_details(value: Any) -> Any:
    """Redact and normalize a value to the package's JSON-compatible subset."""
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
            normalized[key] = normalize_details(item)
        return normalized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_details(item) for item in value]
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
        redact_sensitive(value),
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
            "details": sanitized_details(
                event.get("details_raw", event.get("details"))
            ),
            "schema_version": event.get("schema_version", 0),
            "integrity_digest": event.get("integrity_digest"),
        }
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


def event_digest(event: Any, previous_digest: str = "") -> str:
    import hashlib

    payload = {"previous_digest": previous_digest, "event": event_row(event)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def export_rows(events: list[Any]) -> list[dict[str, Any]]:
    return [event_row(event) for event in events]
