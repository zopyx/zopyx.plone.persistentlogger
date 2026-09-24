"""Backend independent storage contract for the persistent audit log.

All governance semantics (integrity hash chain, retention previews, deletion
bookkeeping and the governance journal) live here as template methods on top
of a small set of primitive operations.  Backends therefore only differ in
*where* records are kept, never in *what* they mean, which is what allows the
same contract test suite to run unchanged against the ZODB annotation backend
and the RDBMS backend.

Canonical record shape
----------------------

Event records are plain dictionaries carrying both the current schema keys
and the legacy keys the original logger used, so every existing consumer
(templates, exporters, the legacy adapter) keeps working with either backend::

    event_id, created_at, actor, event_type, severity, target, comment,
    info_url, details, schema_version, previous_digest, integrity_digest,
    uuid, date, username, level, details_raw

Governance journal records are dictionaries with ``event_id``, ``created_at``,
``actor``, ``action``, ``reason``, the caller supplied payload keys,
``previous_digest`` and ``integrity_digest``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from ..models import (
    DeletionPreview,
    DeletionResult,
    LogEvent,
    RetentionPolicy,
    utc_now,
)
from ..serialization import canonical_json, event_digest, event_row
from .query import (
    Condition,
    ConditionGroup,
    SearchResult,
    SortSpec,
    matches,
    quick_matches,
    sort_entries,
)
from .records import event_date, event_id_of, severity_value

__all__ = [
    "BaseLogStorage",
    "StorageConfigurationError",
    "event_date",
    "event_id_of",
    "new_event_entry",
    "new_governance_entry",
    "object_uid",
    "SearchResult",
    "selection_digest",
    "severity_value",
]


class StorageConfigurationError(RuntimeError):
    """Raised when the configured storage backend cannot be used."""


def object_uid(context: Any) -> str:
    """Return a stable identifier for the object owning a log."""
    try:
        from plone.uuid.interfaces import IUUID

        value = IUUID(context, None)
        if value:
            return value
    except Exception:
        pass
    absolute_url = getattr(context, "absolute_url", None)
    if callable(absolute_url):
        return str(absolute_url())
    return str(getattr(context, "__name__", "unknown"))


def new_event_entry(event: Any, previous_digest: str = "") -> dict[str, Any]:
    """Build a canonical event record including its chain digest."""
    entry: dict[str, Any] = event_row(event)
    entry.update(
        uuid=str(event.event_id),
        date=event.created_at,
        username=event.actor,
        level=getattr(event.severity, "value", event.severity),
        details_raw=event.details,
        previous_digest=previous_digest,
        integrity_digest=event_digest(event, previous_digest),
    )
    return entry


def new_governance_entry(
    action: str, actor: str, reason: str, previous_digest: str = "", **data: object
) -> dict[str, Any]:
    """Build a canonical governance journal record including its digest."""
    entry: dict[str, Any] = {
        "event_id": str(uuid4()),
        "created_at": utc_now(),
        "actor": actor,
        "action": action,
        "reason": reason,
        **data,
    }
    entry["previous_digest"] = previous_digest
    entry["integrity_digest"] = event_digest(entry, previous_digest)
    return entry


def selection_digest(
    object_uid_value: str, ids: tuple[UUID, ...], cutoff: datetime
) -> str:
    """Return the digest binding a deletion preview to its selection."""
    selection = canonical_json(
        {"object": object_uid_value, "ids": ids, "cutoff": cutoff}
    )
    return sha256(selection.encode("utf-8")).hexdigest()


class BaseLogStorage(ABC):
    """Template implementation of the audit log storage contract."""

    def __init__(self, context: Any):
        self.context = context

    def object_uid(self) -> str:
        """Return the identifier this log is scoped to."""
        return object_uid(self.context)

    # ------------------------------------------------------------------
    # primitives implemented by the backends
    # ------------------------------------------------------------------
    @abstractmethod
    def _load_events(self) -> list[dict[str, Any]]:
        """Return every event record of this object (unordered)."""

    @abstractmethod
    def _store_event(self, entry: dict[str, Any]) -> None:
        """Persist one event record, replacing a record with the same id."""

    @abstractmethod
    def _delete_events(self, event_ids: tuple[UUID, ...]) -> tuple[int, int]:
        """Delete records and return ``(deleted, missing)``."""

    @abstractmethod
    def _remove_all_events(self) -> None:
        """Remove every event record of this object."""

    @abstractmethod
    def _load_journal(self) -> list[dict[str, Any]]:
        """Return every governance record of this object (unordered)."""

    @abstractmethod
    def _store_governance(self, entry: dict[str, Any]) -> None:
        """Persist one governance record."""

    @abstractmethod
    def _load_policy(self) -> dict[str, Any] | None:
        """Return the stored retention policy mapping or ``None``."""

    @abstractmethod
    def _store_policy(self, policy: RetentionPolicy) -> None:
        """Persist the retention policy of this object."""

    @abstractmethod
    def _store_preview(self, preview: DeletionPreview) -> None:
        """Persist a deletion preview."""

    @abstractmethod
    def _load_preview(self, operation_id: str) -> DeletionPreview | None:
        """Return a stored deletion preview of this object."""

    def _load_event(self, event_id: str) -> dict[str, Any] | None:
        """Return a single event record. Backends may override for speed."""
        return next(
            (entry for entry in self.events() if event_id_of(entry) == event_id),
            None,
        )

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def events(self) -> list[dict[str, Any]]:
        """Return all event records of this object in chronological order.

        Ordering is fully deterministic (timestamp, then record id), which
        keeps the integrity chain reproducible on every backend.
        """
        records = [entry for entry in self._load_events() if isinstance(entry, dict)]
        return sorted(
            records, key=lambda entry: (event_date(entry), event_id_of(entry))
        )

    def search(
        self,
        conditions: Condition | ConditionGroup | None = None,
        sort: tuple[SortSpec, ...] = (),
        offset: int = 0,
        limit: int | None = None,
        quick: str = "",
    ) -> SearchResult:
        """Return one page of event records plus the number of matches.

        The query model (agGrid's sort/filter payloads) is evaluated here in
        Python; the RDBMS backend overrides this with an equivalent query so
        that paging happens in the database instead of in the application.
        """
        selected = [
            entry
            for entry in self.events()
            if matches(entry, conditions) and quick_matches(entry, quick)
        ]
        ordered = sort_entries(selected, sort) if sort else selected
        total = len(ordered)
        offset = max(offset, 0)
        page = (
            ordered[offset:]
            if limit is None
            else ordered[offset : offset + max(limit, 0)]
        )
        return SearchResult(tuple(page), total)

    def append(self, event: LogEvent) -> dict[str, Any]:
        """Append one event and return the stored record."""
        entry = new_event_entry(event, self.last_digest())
        self._store_event(entry)
        return entry

    def get(self, event_id: Any) -> dict[str, Any] | None:
        """Return one event record by id or ``None``."""
        return self._load_event(str(event_id))

    def clear(self) -> None:
        """Remove every event record of this object."""
        self._remove_all_events()

    def last_digest(self) -> str:
        """Return the integrity digest of the most recent event."""
        entries = self.events()
        return str(entries[-1].get("integrity_digest", "")) if entries else ""

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------
    def policy(self) -> RetentionPolicy:
        """Return the retention policy of this object."""
        value = self._load_policy()
        return RetentionPolicy(**dict(value)) if value else RetentionPolicy()

    def set_policy(self, policy: RetentionPolicy) -> None:
        """Store the retention policy of this object."""
        self._store_policy(policy)

    def preview_delete(
        self, policy: RetentionPolicy, now: datetime | None = None
    ) -> DeletionPreview:
        """Build and store a deletion preview for the current log."""
        now = now or utc_now()
        cutoff = now - timedelta(days=policy.older_than_days)
        eligible = [entry for entry in self.events() if event_date(entry) < cutoff][
            : policy.max_entries
        ]
        ids = tuple(
            UUID(event_id_of(entry)) for entry in eligible if event_id_of(entry)
        )
        uid = object_uid(self.context)
        preview = DeletionPreview(
            uuid4(), uid, cutoff, ids, selection_digest(uid, ids, cutoff)
        )
        self._store_preview(preview)
        return preview

    def delete_preview(self, preview: DeletionPreview, reason: str) -> DeletionResult:
        """Execute a stored deletion preview."""
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        stored = self._load_preview(str(preview.operation_id))
        if stored is None or stored.selection_digest != preview.selection_digest:
            raise ValueError("deletion preview is missing or stale")
        deleted, missing = self._delete_events(preview.event_ids)
        return DeletionResult(
            preview.operation_id,
            len(preview.event_ids),
            len(preview.event_ids),
            deleted,
            missing,
            0,
            reason,
        )

    def get_preview(self, operation_id: Any) -> DeletionPreview | None:
        """Return a stored deletion preview or ``None``."""
        return self._load_preview(str(operation_id))

    # ------------------------------------------------------------------
    # governance journal
    # ------------------------------------------------------------------
    def journal(self) -> list[dict[str, Any]]:
        """Return all governance records of this object in chronological order."""
        records = [entry for entry in self._load_journal() if isinstance(entry, dict)]
        return sorted(
            records, key=lambda entry: (event_date(entry), event_id_of(entry))
        )

    def record_governance(
        self, action: str, actor: str, reason: str, **data: object
    ) -> dict[str, Any]:
        """Append one governance record and return the stored entry."""
        entries = self.journal()
        previous = str(entries[-1].get("integrity_digest", "")) if entries else ""
        entry = new_governance_entry(action, actor, reason, previous, **data)
        self._store_governance(entry)
        return entry
