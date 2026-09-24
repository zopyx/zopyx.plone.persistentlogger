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
from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Lock, RLock
from typing import Any
from uuid import UUID, uuid4

from ..models import (
    DeletionPreview,
    DeletionResult,
    LogEvent,
    RetentionPolicy,
    utc_now,
)
from ..serialization import canonical_json, event_row
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
    "event_digest",
    "event_id_of",
    "governance_digest",
    "new_event_entry",
    "new_governance_entry",
    "object_uid",
    "SearchResult",
    "selection_digest",
    "severity_value",
    "verify_event_chain",
    "verify_governance_chain",
]


class StorageConfigurationError(RuntimeError):
    """Raised when the configured storage backend cannot be used."""


_locks: dict[tuple[str, str], RLock] = {}
_locks_guard = Lock()


def _lock_for(namespace: str, uid: str) -> RLock:
    key = (namespace, uid)
    with _locks_guard:
        return _locks.setdefault(key, RLock())


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


def _event_payload(entry: Any, previous_digest: str | None = None) -> dict[str, Any]:
    """Return the digest payload without legacy aliases or the digest itself."""
    if isinstance(entry, dict):
        row = {
            "event_id": entry.get("event_id", entry.get("uuid", "")),
            "created_at": entry.get("created_at", entry.get("date")),
            "actor": entry.get("actor", entry.get("username", "")),
            "event_type": entry.get("event_type", "application"),
            "severity": entry.get("severity", entry.get("level", "info")),
            "target": entry.get("target", ""),
            "comment": entry.get("comment", ""),
            "info_url": entry.get("info_url"),
            "details": entry.get("details", entry.get("details_raw")),
            "schema_version": entry.get("schema_version", 1),
        }
    else:
        row = event_row(entry)
    previous = (
        previous_digest
        if previous_digest is not None
        else str(entry.get("previous_digest", ""))
        if isinstance(entry, dict)
        else ""
    )
    return {
        "event_id": str(row["event_id"]),
        "created_at": row["created_at"],
        "actor": str(row["actor"] or ""),
        "event_type": str(row["event_type"] or ""),
        "severity": getattr(row["severity"], "value", row["severity"]),
        "target": str(row["target"] or ""),
        "comment": str(row["comment"] or ""),
        "info_url": row["info_url"],
        "details": row["details"],
        "schema_version": int(row["schema_version"] or 1),
        "previous_digest": previous,
    }


def event_digest(event: Any, previous_digest: str | None = None) -> str:
    """Return the canonical, non-self-referential event digest."""
    payload = _event_payload(event, previous_digest)
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


_GOVERNANCE_FIELDS = frozenset(
    {"event_id", "created_at", "actor", "action", "reason", "previous_digest"}
)


def _governance_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical governance payload, including all caller data."""
    return {
        "event_id": str(entry.get("event_id", "")),
        "created_at": entry.get("created_at"),
        "actor": str(entry.get("actor", "")),
        "action": str(entry.get("action", "")),
        "reason": str(entry.get("reason", "")),
        "payload": {
            key: value
            for key, value in entry.items()
            if key not in _GOVERNANCE_FIELDS | {"integrity_digest"}
        },
        "previous_digest": str(entry.get("previous_digest", "")),
    }


def governance_digest(entry: dict[str, Any]) -> str:
    """Return the canonical governance digest over metadata and payload."""
    return sha256(
        canonical_json(_governance_payload(entry)).encode("utf-8")
    ).hexdigest()


def _verify_chain(
    records: list[dict[str, Any]], digest: Callable[[dict[str, Any]], str]
) -> bool:
    if not records:
        return True
    if any(not isinstance(record, dict) for record in records):
        return False
    if len({event_id_of(record) for record in records}) != len(records):
        return False
    if any(
        not record.get("integrity_digest")
        or digest(record) != record.get("integrity_digest")
        for record in records
    ):
        return False
    first = [record for record in records if not record.get("previous_digest")]
    if len(first) != 1:
        return False
    by_previous = {str(record.get("previous_digest", "")): record for record in records}
    current = first[0]
    visited: set[str] = set()
    while True:
        record_id = event_id_of(current)
        if record_id in visited:
            return False
        visited.add(record_id)
        successor = by_previous.get(str(current["integrity_digest"]))
        if successor is None:
            break
        current = successor
    return len(visited) == len(records)


def verify_event_chain(records: list[dict[str, Any]]) -> bool:
    """Verify event contents, links, and that no chain element is missing."""
    return _verify_chain(records, event_digest)


def verify_governance_chain(records: list[dict[str, Any]]) -> bool:
    """Verify governance contents, links, and that no chain element is missing."""
    return _verify_chain(records, governance_digest)


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
        integrity_digest="",
    )
    entry["integrity_digest"] = event_digest(entry, previous_digest)
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
        **{key: value for key, value in data.items() if key not in _GOVERNANCE_FIELDS},
    }
    entry["previous_digest"] = previous_digest
    entry["integrity_digest"] = governance_digest(entry)
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

    def _rewrite_event(self, entry: dict[str, Any]) -> None:
        """Persist a changed digest while rebuilding a canonical chain."""

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

    def _consume_preview(self, preview: DeletionPreview) -> DeletionPreview | None:
        """Consume a preview; persistent backends override this atomically."""
        stored = self._load_preview(str(preview.operation_id))
        return stored if stored == preview else None

    def _store_event_head(self, entry: dict[str, Any]) -> None:
        """Persist the event chain head when the backend needs one."""

    def _store_governance_head(self, entry: dict[str, Any]) -> None:
        """Persist the governance chain head when the backend needs one."""

    def _load_event_head(self) -> str:
        """Return the persisted event chain head, if available."""
        return ""

    def _reset_event_head(self) -> None:
        """Reset the event head after an intentional retention deletion."""

    def _load_governance_head(self) -> str:
        """Return the persisted governance chain head, if available."""
        return ""

    def _lock_namespace(self) -> str:
        return type(self).__name__

    def _lock(self, kind: str) -> RLock:
        return _lock_for(f"{self._lock_namespace()}:{kind}", self.object_uid())

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

    @staticmethod
    def _chain_tail(records: list[dict[str, Any]]) -> str:
        """Find a chain tail without using event timestamps as order."""
        if not records:
            return ""
        by_previous = {
            str(entry.get("previous_digest", "")): entry for entry in records
        }
        current = by_previous.get("")
        if current is None:
            return ""
        visited: set[str] = set()
        while event_id_of(current) not in visited:
            visited.add(event_id_of(current))
            successor = by_previous.get(str(current.get("integrity_digest", "")))
            if successor is None:
                return str(current.get("integrity_digest", ""))
            current = successor
        return ""

    def _rebuild_event_chain(self) -> str:
        """Re-link events in deterministic timestamp/id order."""
        records = [entry for entry in self._load_events() if isinstance(entry, dict)]
        ordered = sorted(
            records, key=lambda entry: (event_date(entry), event_id_of(entry))
        )
        previous = ""
        for entry in ordered:
            entry["previous_digest"] = previous
            entry["integrity_digest"] = event_digest(entry)
            self._rewrite_event(entry)
            previous = entry["integrity_digest"]
        if ordered:
            self._store_event_head(ordered[-1])
        return previous

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
        previous = self.last_digest()
        with self._lock("events"):
            if self.get(event.event_id) is not None:
                raise ValueError(f"event id {event.event_id} already exists")
            entry = new_event_entry(event, previous)
            self._store_event(entry)
            self._rebuild_event_chain()
            return entry

    def get(self, event_id: Any) -> dict[str, Any] | None:
        """Return one event record by id or ``None``."""
        return self._load_event(str(event_id))

    def clear(self) -> None:
        """Remove every event record of this object."""
        self._remove_all_events()
        self._reset_event_head()

    def last_digest(self) -> str:
        """Return the integrity digest of the append-only event head."""
        return self._load_event_head() or self._chain_tail(self._load_events())

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
        """Execute and consume a stored deletion preview exactly once."""
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        with self._lock("events"):
            stored = self._load_preview(str(preview.operation_id))
            if (
                stored is None
                or stored != preview
                or stored.object_uid != self.object_uid()
                or stored.selection_digest
                != selection_digest(stored.object_uid, stored.event_ids, stored.cutoff)
            ):
                raise ValueError("deletion preview is missing or stale")
            consumed = self._consume_preview(stored)
            if consumed is None:
                raise ValueError("deletion preview is missing or stale")
            deleted, missing = self._delete_events(stored.event_ids)
            self._reset_event_head()
            return DeletionResult(
                stored.operation_id,
                len(stored.event_ids),
                len(stored.event_ids),
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
        with self._lock("governance"):
            entry = new_governance_entry(
                action, actor, reason, self._governance_tail(), **data
            )
            self._store_governance(entry)
            self._store_governance_head(entry)
            return entry

    def _governance_tail(self) -> str:
        return self._load_governance_head() or self._chain_tail(self._load_journal())
