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
from threading import Lock, RLock
from typing import Any
from uuid import UUID, uuid4

from ..models import (
    PREVIEW_TTL,
    DeletionPreview,
    DeletionResult,
    LogEvent,
    RetentionPolicy,
    require_utc,
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
    "event_digest",
    "event_id_of",
    "governance_digest",
    "new_event_entry",
    "new_governance_entry",
    "next_sequence",
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
    from hashlib import sha256

    return sha256(
        canonical_json(_governance_payload(entry)).encode("utf-8")
    ).hexdigest()


def _verify_chain(
    records: list[dict[str, Any]],
    digest: Callable[[dict[str, Any]], str],
    anchor_digest: str | None = None,
) -> bool:
    if not records:
        return anchor_digest in (None, "")
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
    if anchor_digest is not None and first[0].get("integrity_digest") != anchor_digest:
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


def verify_event_chain(
    records: list[dict[str, Any]], anchor_digest: str | None = None
) -> bool:
    """Verify events, optionally against one retention-chain anchor."""
    return _verify_chain(records, event_digest, anchor_digest)


def verify_governance_chain(records: list[dict[str, Any]]) -> bool:
    """Verify governance contents, links, and that no chain element is missing."""
    return _verify_chain(records, governance_digest)


def new_event_entry(
    event: Any, previous_digest: str = "", sequence: int | None = None
) -> dict[str, Any]:
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
    if sequence is not None:
        if sequence <= 0:
            raise ValueError("event sequence must be positive")
        entry["sequence"] = sequence
    entry["integrity_digest"] = event_digest(entry, previous_digest)
    return entry


def next_sequence(records: list[dict[str, Any]]) -> int:
    """Return the next strictly increasing sequence for a record collection."""
    values = [
        int(record["sequence"])
        for record in records
        if isinstance(record.get("sequence"), int)
        and not isinstance(record.get("sequence"), bool)
        and int(record["sequence"]) > 0
    ]
    return max(values, default=0) + 1


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
    from hashlib import sha256

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
        """Persist a changed digest during explicit chain repair."""

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
        chain_order = self._chain_order(records)
        chain_rank = {
            event_id_of(entry): index for index, entry in enumerate(chain_order)
        }
        return sorted(
            records,
            key=lambda entry: (
                event_date(entry),
                chain_rank.get(event_id_of(entry), len(records)),
                event_id_of(entry),
            ),
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

    @staticmethod
    def _chain_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return records in link order, falling back for malformed data."""
        if not records:
            return []
        by_previous: dict[str, dict[str, Any]] = {}
        for entry in records:
            previous = str(entry.get("previous_digest", ""))
            if previous in by_previous:
                return sorted(
                    records, key=lambda value: (event_date(value), event_id_of(value))
                )
            by_previous[previous] = entry
        current = by_previous.get("")
        if current is None:
            return sorted(
                records, key=lambda value: (event_date(value), event_id_of(value))
            )
        ordered: list[dict[str, Any]] = []
        visited: set[str] = set()
        while current is not None and event_id_of(current) not in visited:
            visited.add(event_id_of(current))
            ordered.append(current)
            current = by_previous.get(str(current.get("integrity_digest", "")))
        if len(ordered) != len(records):
            return sorted(
                records, key=lambda value: (event_date(value), event_id_of(value))
            )
        return ordered

    def event_chain_anchor(self) -> str:
        """Return the digest of the first event in the current chain."""
        records = [entry for entry in self._load_events() if isinstance(entry, dict)]
        ordered = self._chain_order(records)
        return str(ordered[0].get("integrity_digest", "")) if ordered else ""

    def _relink_event_chain(self, records: list[dict[str, Any]]) -> tuple[str, str]:
        """Relink survivors after explicit retention deletion or repair."""
        ordered = records
        previous = ""
        anchor = ""
        for entry in ordered:
            entry["previous_digest"] = previous
            entry["integrity_digest"] = event_digest(entry)
            self._rewrite_event(entry)
            anchor = anchor or str(entry["integrity_digest"])
            previous = str(entry["integrity_digest"])
        if ordered:
            self._store_event_head(ordered[-1])
        else:
            self._reset_event_head()
        return anchor, previous

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
        with self._lock("events"):
            if self.get(event.event_id) is not None:
                raise ValueError(f"event id {event.event_id} already exists")
            previous = self.last_digest()
            entry = new_event_entry(event, previous, next_sequence(self._load_events()))
            self._store_event(entry)
            self._store_event_head(entry)
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
        now = require_utc(now or utc_now())
        cutoff = now - timedelta(days=policy.older_than_days)
        eligible = [entry for entry in self.events() if event_date(entry) < cutoff][
            : policy.max_entries
        ]
        ids = tuple(
            UUID(event_id_of(entry)) for entry in eligible if event_id_of(entry)
        )
        uid = object_uid(self.context)
        preview = DeletionPreview(
            uuid4(),
            uid,
            cutoff,
            ids,
            selection_digest(uid, ids, cutoff),
            now + PREVIEW_TTL,
        )
        self._store_preview(preview)
        return preview

    def cleanup_expired_previews(self, now: datetime | None = None) -> int:
        """Remove expired unconsumed previews for this object."""
        return 0

    def remove_object(self) -> int:
        """Remove external lifecycle data for this object, if any."""
        return 0

    def _validated_preview(
        self, preview: DeletionPreview, now: datetime | None = None
    ) -> DeletionPreview:
        """Return a stored, unexpired preview after validating its selection."""
        current = require_utc(now) if now is not None else None
        stored = self._load_preview(str(preview.operation_id))
        if stored is not None and current is not None and stored.is_expired(current):
            self.cleanup_expired_previews(current)
            stored = None
        if (
            stored is None
            or stored != preview
            or stored.object_uid != self.object_uid()
            or stored.selection_digest
            != selection_digest(stored.object_uid, stored.event_ids, stored.cutoff)
        ):
            raise ValueError("deletion preview is missing or stale")
        return stored

    def _deletion_result(
        self,
        preview: DeletionPreview,
        reason: str,
        deleted: int,
        missing: int,
        failed: int = 0,
    ) -> DeletionResult:
        """Build a result whose counters describe the attempted operation."""
        return DeletionResult(
            preview.operation_id,
            len(preview.event_ids),
            len(preview.event_ids),
            deleted,
            missing,
            failed,
            reason,
        )

    def delete_preview(
        self,
        preview: DeletionPreview,
        reason: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Execute and consume a stored deletion preview exactly once."""
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        with self._lock("events"):
            stored = self._validated_preview(preview, now)
            consumed = self._consume_preview(stored)
            if consumed is None:
                raise ValueError("deletion preview is missing or stale")
            records = [
                entry for entry in self._load_events() if isinstance(entry, dict)
            ]
            selected = {str(event_id) for event_id in stored.event_ids}
            deleted, missing = self._delete_events(stored.event_ids)
            if deleted:
                survivors = [
                    entry
                    for entry in self._chain_order(records)
                    if event_id_of(entry) not in selected
                ]
                self._relink_event_chain(survivors)
            else:
                self._reset_event_head()
            return self._deletion_result(stored, reason, deleted, missing)

    def delete_and_journal(
        self,
        preview: DeletionPreview,
        reason: str,
        actor: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Delete a preview and write its evidence as one repository operation.

        Backends with transactional storage override this method when they need
        to place all primitive writes in one database transaction.  The base
        implementation deliberately performs both changes while holding the
        repository locks; the ZODB backend therefore keeps its historical
        surrounding-transaction semantics.
        """
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        with self._lock("events"), self._lock("governance"):
            stored = self._validated_preview(preview, now)
            consumed = self._consume_preview(stored)
            if consumed is None:
                raise ValueError("deletion preview is missing or stale")
            records = [
                entry for entry in self._load_events() if isinstance(entry, dict)
            ]
            selected = {str(event_id) for event_id in stored.event_ids}
            deleted, missing = self._delete_events(stored.event_ids)
            survivor_digest = ""
            if deleted:
                survivors = [
                    entry
                    for entry in self._chain_order(records)
                    if event_id_of(entry) not in selected
                ]
                survivor_digest, _ = self._relink_event_chain(survivors)
            else:
                self._reset_event_head()
            result = self._deletion_result(stored, reason, deleted, missing)
            entry = new_governance_entry(
                "retention_delete",
                actor,
                reason,
                self._governance_tail(),
                operation_id=str(result.operation_id),
                requested=result.requested,
                eligible=result.eligible,
                deleted=result.deleted,
                missing=result.missing,
                failed=result.failed,
                survivor_digest=survivor_digest,
            )
            self._store_governance(entry)
            self._store_governance_head(entry)
            return result

    def get_preview(
        self, operation_id: Any, now: datetime | None = None
    ) -> DeletionPreview | None:
        """Return an unexpired stored deletion preview or ``None``."""
        if now is not None:
            self.cleanup_expired_previews(now)
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
