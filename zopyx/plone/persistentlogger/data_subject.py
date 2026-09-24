"""Data-subject search, legal holds, and bounded journal export.

The public functions deliberately accept an explicit iterable of Plone objects.
That makes the object scope visible to the caller and avoids an accidental
site-wide scan of records that the caller is not allowed to inspect.  A
site-wide index or traversal policy can be supplied by deployment code later;
the core never invents one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from persistent.mapping import PersistentMapping
from zope.annotation.interfaces import IAnnotations

from .serialization import event_row, json_default
from .storage import event_id_of, get_repository, object_uid

HOLD_KEY = "zopyx.plone.persistentlogger.connector.legal-holds"
DEFAULT_SEARCH_LIMIT = 1_000
DEFAULT_SITE_LIMIT = 10_000
DEFAULT_JOURNAL_LIMIT = 10_000


class QuotaExceeded(ValueError):
    """Raised when a data-subject operation would cross a hard quota."""


@dataclass(frozen=True, slots=True)
class DataSubjectQuery:
    """Exact, case-insensitive data-subject selectors.

    ``actor`` and ``target`` are intentionally separate.  ``event_id`` is an
    exact UUID/string selector and is useful for an access or deletion request
    that already has a stable audit-record identifier.
    """

    actor: str | None = None
    target: str | None = None
    event_id: str | None = None

    def matches(self, entry: dict[str, Any]) -> bool:
        if (
            self.actor is not None
            and str(entry.get("actor", entry.get("username", "")) or "").casefold()
            != self.actor.casefold()
        ):
            return False
        target = str(entry.get("target", "") or "").casefold()
        if self.target is not None and target != self.target.casefold():
            return False
        if (
            self.event_id is not None
            and event_id_of(entry).casefold() != self.event_id.casefold()
        ):
            return False
        return True


@dataclass(frozen=True, slots=True)
class DataSubjectSearchResult:
    """Bounded search result with an explicit object scope."""

    rows: tuple[dict[str, Any], ...]
    total: int
    objects_scanned: int


@dataclass(frozen=True, slots=True)
class LegalHold:
    """An object-scoped legal hold for all or selected event IDs."""

    hold_id: str
    object_uid: str
    actor: str
    reason: str
    created_at: datetime
    event_ids: tuple[str, ...] = ()
    released_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.released_at is None


def _validate_reason(reason: str) -> str:
    value = str(reason).strip()
    if len(value) < 10:
        raise ValueError("legal hold reason must contain at least 10 characters")
    return value


def _hold_store(context: Any, *, create: bool = False) -> Any:
    try:
        annotations = IAnnotations(context)
    except TypeError:
        if create:
            raise ValueError("context does not support legal holds") from None
        return None
    store = annotations.get(HOLD_KEY)
    if store is None and create:
        store = PersistentMapping()
        annotations[HOLD_KEY] = store
    return store


def _hold_from_value(value: Any) -> LegalHold | None:
    if not isinstance(value, Mapping):
        return None
    try:
        created = datetime.fromisoformat(str(value["created_at"]))
        released = value.get("released_at")
        released_at = datetime.fromisoformat(str(released)) if released else None
        return LegalHold(
            hold_id=str(value["hold_id"]),
            object_uid=str(value["object_uid"]),
            actor=str(value["actor"]),
            reason=str(value["reason"]),
            created_at=created,
            event_ids=tuple(str(item) for item in value.get("event_ids", ())),
            released_at=released_at,
        )
    except (KeyError, TypeError, ValueError):
        return None


def holds(context: Any, *, active_only: bool = True) -> tuple[LegalHold, ...]:
    """Return holds stored for one object, ignoring malformed legacy values."""
    store = _hold_store(context)
    if not store:
        return ()
    result = tuple(
        hold
        for hold in (_hold_from_value(value) for value in store.values())
        if hold is not None and (not active_only or hold.active)
    )
    return tuple(sorted(result, key=lambda item: (item.created_at, item.hold_id)))


def is_held(context: Any, event_id: str | UUID | None = None) -> bool:
    """Return whether an object or a selected event is under an active hold."""
    wanted = None if event_id is None else str(event_id)
    return any(
        not hold.event_ids or wanted is None or wanted in hold.event_ids
        for hold in holds(context)
    )


def create_hold(
    context: Any,
    actor: str,
    reason: str,
    *,
    event_ids: Iterable[str | UUID] = (),
) -> LegalHold:
    """Create and journal an object-scoped legal hold."""
    reason = _validate_reason(reason)
    normalized_ids = tuple(sorted({str(item) for item in event_ids}))
    hold = LegalHold(
        hold_id=str(uuid4()),
        object_uid=object_uid(context),
        actor=str(actor),
        reason=reason,
        created_at=datetime.now(UTC),
        event_ids=normalized_ids,
    )
    store = _hold_store(context, create=True)
    repository = get_repository(context)
    with repository.retention_lock():
        store[hold.hold_id] = {
            "hold_id": hold.hold_id,
            "object_uid": hold.object_uid,
            "actor": hold.actor,
            "reason": hold.reason,
            "created_at": hold.created_at.isoformat(),
            "event_ids": list(hold.event_ids),
            "released_at": None,
        }
        try:
            repository.record_governance(
                "legal_hold_created",
                hold.actor,
                hold.reason,
                hold_id=hold.hold_id,
                object_uid=hold.object_uid,
                event_count=len(hold.event_ids),
            )
        except Exception:
            del store[hold.hold_id]
            raise
    return hold


def release_hold(context: Any, hold_id: str, actor: str, reason: str) -> LegalHold:
    """Release one active hold and append a governance record."""
    reason = _validate_reason(reason)
    store = _hold_store(context)
    repository = get_repository(context)
    with repository.retention_lock():
        if not store or hold_id not in store:
            raise ValueError("legal hold does not exist")
        current = _hold_from_value(store[hold_id])
        if current is None or not current.active:
            raise ValueError("legal hold is already released or invalid")
        released_at = datetime.now(UTC)
        store[hold_id]["released_at"] = released_at.isoformat()
        try:
            repository.record_governance(
                "legal_hold_released",
                str(actor),
                reason,
                hold_id=hold_id,
                object_uid=current.object_uid,
            )
        except Exception:
            store[hold_id]["released_at"] = None
            raise
    return LegalHold(
        current.hold_id,
        current.object_uid,
        current.actor,
        current.reason,
        current.created_at,
        current.event_ids,
        released_at,
    )


def search_data_subject(
    contexts: Iterable[Any],
    query: DataSubjectQuery,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    site_limit: int = DEFAULT_SITE_LIMIT,
) -> DataSubjectSearchResult:
    """Search explicitly supplied objects, never an implicit whole site.

    Rows are returned with ``object_uid`` so an access/export response cannot
    lose which object supplied a matching event.  ``limit`` is a hard result
    bound; the function still reports the bounded total for quota enforcement.
    """
    if not 0 < limit <= DEFAULT_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {DEFAULT_SEARCH_LIMIT}")
    if not 0 < site_limit <= DEFAULT_SITE_LIMIT:
        raise ValueError(f"site_limit must be between 1 and {DEFAULT_SITE_LIMIT}")
    rows: list[dict[str, Any]] = []
    total = 0
    scanned = 0
    seen_uids: set[str] = set()
    for context in contexts:
        uid = object_uid(context)
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        scanned += 1
        for entry in get_repository(context).events():
            if not query.matches(entry):
                continue
            total += 1
            if total > site_limit:
                raise QuotaExceeded("data-subject search exceeds the site quota")
            if len(rows) >= limit:
                continue
            row = event_row(entry)
            row["object_uid"] = uid
            row["legal_hold"] = is_held(context, event_id_of(entry))
            rows.append(row)
    return DataSubjectSearchResult(tuple(rows), total, scanned)


def export_journal(
    contexts: Iterable[Any], *, limit: int = DEFAULT_JOURNAL_LIMIT
) -> bytes:
    """Export governance journal rows from explicitly supplied objects as JSON."""
    if not 0 < limit <= DEFAULT_JOURNAL_LIMIT:
        raise ValueError(f"limit must be between 1 and {DEFAULT_JOURNAL_LIMIT}")
    records: list[dict[str, Any]] = []
    seen_uids: set[str] = set()
    for context in contexts:
        uid = object_uid(context)
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        for entry in get_repository(context).journal():
            row = dict(entry)
            row["object_uid"] = uid
            records.append(row)
            if len(records) > limit:
                raise QuotaExceeded("journal export exceeds the site quota")
    return json.dumps(
        {"schema_version": 1, "records": records},
        default=json_default,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "DEFAULT_JOURNAL_LIMIT",
    "DEFAULT_SEARCH_LIMIT",
    "DEFAULT_SITE_LIMIT",
    "DataSubjectQuery",
    "DataSubjectSearchResult",
    "HOLD_KEY",
    "LegalHold",
    "QuotaExceeded",
    "create_hold",
    "export_journal",
    "holds",
    "is_held",
    "release_hold",
    "search_data_subject",
]
