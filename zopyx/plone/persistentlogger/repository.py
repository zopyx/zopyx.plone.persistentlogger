"""Persistent repositories for object logs and governance journal entries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from BTrees.OOBTree import OOBTree
from persistent.mapping import PersistentMapping
from zope.annotation.interfaces import IAnnotations

from .migrations.v1 import migrate_store
from .models import DeletionPreview, DeletionResult, LogEvent, RetentionPolicy, utc_now
from .serialization import canonical_json, event_digest, event_row

LOG_KEY = "zopyx.plone.persistentlogger.connector.log"
JOURNAL_KEY = "zopyx.plone.persistentlogger.connector.governance"
POLICY_KEY = "zopyx.plone.persistentlogger.connector.retention"
PREVIEW_KEY = "zopyx.plone.persistentlogger.connector.previews"


def object_uid(context: Any) -> str:
    try:
        from plone.uuid.interfaces import IUUID

        value = IUUID(context, None)
        if value:
            return value
    except Exception:
        pass
    absolute_url = getattr(context, "absolute_url", None)
    return (
        absolute_url()
        if callable(absolute_url)
        else str(getattr(context, "__name__", "unknown"))
    )


def _event_id(entry: dict[str, Any]) -> str:
    return str(entry.get("uuid", entry.get("event_id", "")))


def _event_date(entry: dict[str, Any]) -> datetime:
    value = entry.get("date", entry.get("created_at"))
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AnnotationRepository:
    """Repository preserving legacy annotation records while adding typed APIs."""

    def __init__(self, context: Any):
        self.context = context

    @property
    def annotations(self) -> Any:
        annotations = IAnnotations(self.context)
        if LOG_KEY not in annotations:
            annotations[LOG_KEY] = OOBTree()
        store = annotations[LOG_KEY]
        migrate_store(store)
        return store

    def events(self) -> list[dict[str, Any]]:
        return [value for value in self.annotations.values() if isinstance(value, dict)]

    def append(self, event: LogEvent) -> dict[str, Any]:
        previous = self._last_digest()
        entry: dict[str, Any] = event_row(event)
        entry.update(
            uuid=str(event.event_id),
            date=event.created_at,
            username=event.actor,
            level=getattr(event.severity, "value", event.severity),
            details_raw=event.details,
            previous_digest=previous,
            integrity_digest=event_digest(event, previous),
        )
        self.annotations[entry["uuid"]] = entry
        self.annotations._p_changed = True
        return entry

    def _last_digest(self) -> str:
        entries = self.events()
        if not entries:
            return ""
        latest = max(entries, key=_event_date)
        return str(latest.get("integrity_digest", ""))

    def get(self, event_id: str) -> dict[str, Any] | None:
        try:
            value = self.annotations.get(event_id)
        except (KeyError, TypeError):
            value = None
        if isinstance(value, dict):
            return value
        return next(
            (entry for entry in self.events() if _event_id(entry) == event_id), None
        )

    def preview_delete(
        self, policy: RetentionPolicy, now: datetime | None = None
    ) -> DeletionPreview:
        now = now or utc_now()
        cutoff = now - timedelta(days=policy.older_than_days)
        eligible = [entry for entry in self.events() if _event_date(entry) < cutoff]
        eligible.sort(key=_event_date)
        ids = tuple(
            UUID(_event_id(entry))
            for entry in eligible[: policy.max_entries]
            if _event_id(entry)
        )
        selection = canonical_json(
            {"object": object_uid(self.context), "ids": ids, "cutoff": cutoff}
        )
        digest = sha256(selection.encode("utf-8")).hexdigest()
        preview = DeletionPreview(
            uuid4(), object_uid(self.context), cutoff, ids, digest
        )
        annotations = IAnnotations(self.context)
        previews = annotations.get(PREVIEW_KEY)
        if not isinstance(previews, PersistentMapping):
            previews = PersistentMapping()
            annotations[PREVIEW_KEY] = previews
        previews[str(preview.operation_id)] = preview
        return preview

    def delete_preview(self, preview: DeletionPreview, reason: str) -> DeletionResult:
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        annotations = IAnnotations(self.context)
        previews = annotations.get(PREVIEW_KEY)
        if previews is None:
            previews = {}
        current = previews.get(str(preview.operation_id))
        if current is None or current.selection_digest != preview.selection_digest:
            raise ValueError("deletion preview is missing or stale")
        deleted = 0
        missing = 0
        for event_id in preview.event_ids:
            key = str(event_id)
            if self.annotations.get(key) is not None:
                del self.annotations[key]
                deleted += 1
                continue
            legacy_key = next(
                (
                    key
                    for key, value in self.annotations.items()
                    if _event_id(value) == str(event_id)
                ),
                None,
            )
            if legacy_key is None:
                missing += 1
            else:
                del self.annotations[legacy_key]
                deleted += 1
        self.annotations._p_changed = True
        return DeletionResult(
            preview.operation_id,
            len(preview.event_ids),
            len(preview.event_ids),
            deleted,
            missing,
            0,
            reason,
        )

    def policy(self) -> RetentionPolicy:
        value = IAnnotations(self.context).get(POLICY_KEY)
        return RetentionPolicy(**dict(value)) if value else RetentionPolicy()

    def set_policy(self, policy: RetentionPolicy) -> None:
        IAnnotations(self.context)[POLICY_KEY] = PersistentMapping(
            {
                "enabled": policy.enabled,
                "older_than_days": policy.older_than_days,
                "max_entries": policy.max_entries,
            }
        )

    def journal(self) -> PersistentMapping:
        annotations = IAnnotations(self.context)
        journal = annotations.get(JOURNAL_KEY)
        if not isinstance(journal, PersistentMapping):
            journal = PersistentMapping()
            annotations[JOURNAL_KEY] = journal
        return journal

    def record_governance(
        self, action: str, actor: str, reason: str, **data: object
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "event_id": str(uuid4()),
            "created_at": utc_now(),
            "actor": actor,
            "action": action,
            "reason": reason,
            **data,
        }
        previous = ""
        if self.journal():
            latest = max(self.journal().values(), key=lambda value: value["created_at"])
            previous = str(latest.get("integrity_digest", ""))
        entry["previous_digest"] = previous
        entry["integrity_digest"] = event_digest(entry, previous)
        self.journal()[entry["event_id"]] = entry
        self.journal()._p_changed = True
        return entry
