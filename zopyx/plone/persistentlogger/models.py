"""Typed domain objects used by the governance logger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LogEvent:
    comment: str
    severity: Severity | str = Severity.INFO
    actor: str = ""
    event_type: str = "application"
    target: str = ""
    info_url: str | None = None
    details: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    event_id: UUID = field(default_factory=uuid4)
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    schema_version: int = 1
    integrity_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.comment or len(self.comment) > 4000:
            raise ValueError("comment must contain 1-4000 characters")
        if len(self.event_type) > 100:
            raise ValueError("event_type must contain at most 100 characters")
        if len(self.actor) > 255:
            raise ValueError("actor must contain at most 255 characters")
        if len(self.target) > 2048:
            raise ValueError("target must contain at most 2048 characters")
        if self.info_url is not None and len(self.info_url) > 2048:
            raise ValueError("info_url must contain at most 2048 characters")
        if self.details is not None and len(repr(self.details).encode()) > 65536:
            raise ValueError("details must not exceed 64 KiB")
        object.__setattr__(self, "created_at", require_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    enabled: bool = False
    older_than_days: int = 365
    max_entries: int = 100

    def __post_init__(self) -> None:
        if self.older_than_days <= 0:
            raise ValueError("older_than_days must be positive")
        if not 0 < self.max_entries <= 100:
            raise ValueError("max_entries must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    operation_id: UUID
    object_uid: str
    cutoff: datetime
    event_ids: tuple[UUID, ...]
    selection_digest: str


@dataclass(frozen=True, slots=True)
class DeletionResult:
    operation_id: UUID
    requested: int
    eligible: int
    deleted: int
    missing: int
    failed: int
    reason: str


@dataclass(frozen=True, slots=True)
class ExportRequest:
    format: str
    max_entries: int = 100_000
    max_bytes: int = 1_000_000_000

    def __post_init__(self) -> None:
        if self.format not in {"json", "csv", "xlsx", "ods"}:
            raise ValueError("unsupported export format")
        if not 0 < self.max_entries <= 100_000:
            raise ValueError("max_entries must be between 1 and 100000")
        if not 0 < self.max_bytes <= 1_000_000_000:
            raise ValueError("max_bytes must be between 1 and 1000000000")
