"""ZODB backend: audit records stored in the object's annotations.

This is the historical storage location of the package and remains the
default.  Records live in an ``OOBTree`` inside the annotations of the very
object they describe, so they travel with the object, are versioned by the
ZODB and participate in the surrounding transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import transaction
from BTrees.OOBTree import OOBTree
from persistent.mapping import PersistentMapping
from zope.annotation.interfaces import IAnnotations

from ..migrations.v1 import migrate_store
from ..models import DeletionPreview, DeletionResult, RetentionPolicy
from .base import BaseLogStorage, event_id_of

__all__ = [
    "JOURNAL_KEY",
    "CHAIN_HEAD_KEY",
    "LOG_KEY",
    "QUARANTINE_KEY",
    "migrate_store",
    "POLICY_KEY",
    "PREVIEW_KEY",
    "AnnotationRepository",
]

LOG_KEY = "zopyx.plone.persistentlogger.connector.log"
QUARANTINE_KEY = "zopyx.plone.persistentlogger.connector.log-quarantine"
JOURNAL_KEY = "zopyx.plone.persistentlogger.connector.governance"
POLICY_KEY = "zopyx.plone.persistentlogger.connector.retention"
PREVIEW_KEY = "zopyx.plone.persistentlogger.connector.previews"
CHAIN_HEAD_KEY = "zopyx.plone.persistentlogger.connector.chain-head"


class AnnotationRepository(BaseLogStorage):
    """Repository preserving legacy annotation records while adding typed APIs."""

    def delete_and_journal(
        self, preview: DeletionPreview, reason: str, actor: str
    ) -> DeletionResult:
        """Keep deletion and evidence in the surrounding ZODB transaction."""
        savepoint = transaction.savepoint()
        try:
            return super().delete_and_journal(preview, reason, actor)
        except Exception:
            # The caller owns the outer transaction.  Rolling back this
            # savepoint removes both annotation mutations without aborting
            # unrelated changes in that transaction.
            savepoint.rollback()
            raise

    @property
    def annotations(self) -> Any:
        """Return the annotation store without changing it on read."""
        annotations = IAnnotations(self.context)
        if LOG_KEY not in annotations:
            annotations[LOG_KEY] = OOBTree()
        return annotations[LOG_KEY]

    def delete_and_journal(
        self,
        preview: DeletionPreview,
        reason: str,
        actor: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Keep deletion and its evidence in one surrounding ZODB transaction."""
        savepoint = transaction.savepoint()
        try:
            return super().delete_and_journal(preview, reason, actor, now=now)
        except Exception:
            # Do not leave annotation mutations live when journaling fails.
            # The caller still owns the surrounding transaction; rolling back
            # only this savepoint preserves the historical ZODB semantics.
            savepoint.rollback()
            raise

    # ------------------------------------------------------------------
    def _load_events(self) -> list[dict[str, Any]]:
        return [value for value in self.annotations.values() if isinstance(value, dict)]

    def _load_event(self, event_id: str) -> dict[str, Any] | None:
        try:
            value = self.annotations.get(event_id)
        except (KeyError, TypeError):
            value = None
        if isinstance(value, dict):
            return value
        # Records written by the legacy logger keep their own key.
        return next(
            (entry for entry in self.events() if event_id_of(entry) == event_id), None
        )

    def _store_event(self, entry: dict[str, Any]) -> None:
        store = self.annotations
        if store.get(entry["uuid"]) is not None:
            raise ValueError(f"event id {entry['uuid']} already exists")
        store[entry["uuid"]] = entry
        store._p_changed = True

    def _rewrite_event(self, entry: dict[str, Any]) -> None:
        """Persist a changed digest during retention relinking or repair."""
        store = self.annotations
        store[str(entry["uuid"])] = entry
        store._p_changed = True

    def _load_event_head(self) -> str:
        value = IAnnotations(self.context).get(CHAIN_HEAD_KEY)
        return str(value.get("event_digest", "")) if isinstance(value, dict) else ""

    def _store_event_head(self, entry: dict[str, Any]) -> None:
        IAnnotations(self.context)[CHAIN_HEAD_KEY] = PersistentMapping(
            {
                "event_digest": str(entry["integrity_digest"]),
                "event_id": str(entry["event_id"]),
            }
        )

    def _reset_event_head(self) -> None:
        annotations = IAnnotations(self.context)
        value = annotations.get(CHAIN_HEAD_KEY)
        head = dict(value) if isinstance(value, dict) else {}
        head["event_digest"] = self._chain_tail(self._load_events())
        annotations[CHAIN_HEAD_KEY] = PersistentMapping(head)

    def _delete_events(self, event_ids: tuple[UUID, ...]) -> tuple[int, int]:
        store = self.annotations
        deleted = 0
        missing = 0
        for event_id in event_ids:
            key = str(event_id)
            if store.get(key) is not None:
                del store[key]
                deleted += 1
                continue
            legacy_key = next(
                (
                    candidate
                    for candidate, value in store.items()
                    if isinstance(value, dict) and event_id_of(value) == key
                ),
                None,
            )
            if legacy_key is None:
                missing += 1
            else:
                del store[legacy_key]
                deleted += 1
        if deleted:
            store._p_changed = True
        return deleted, missing

    def _remove_all_events(self) -> None:
        IAnnotations(self.context)[LOG_KEY] = OOBTree()

    # ------------------------------------------------------------------
    # governance journal primitives
    # ------------------------------------------------------------------
    def _journal_store(self) -> PersistentMapping:
        annotations = IAnnotations(self.context)
        journal = annotations.get(JOURNAL_KEY)
        if not isinstance(journal, PersistentMapping):
            journal = PersistentMapping()
            annotations[JOURNAL_KEY] = journal
        return journal

    def _load_journal(self) -> list[dict[str, Any]]:
        return list(self._journal_store().values())

    def _store_governance(self, entry: dict[str, Any]) -> None:
        store = self._journal_store()
        store[entry["event_id"]] = entry
        store._p_changed = True

    def _load_governance_head(self) -> str:
        value = IAnnotations(self.context).get(CHAIN_HEAD_KEY)
        return (
            str(value.get("governance_digest", "")) if isinstance(value, dict) else ""
        )

    def _store_governance_head(self, entry: dict[str, Any]) -> None:
        annotations = IAnnotations(self.context)
        value = annotations.get(CHAIN_HEAD_KEY)
        head = dict(value) if isinstance(value, dict) else {}
        head["governance_digest"] = str(entry["integrity_digest"])
        head["governance_event_id"] = str(entry["event_id"])
        annotations[CHAIN_HEAD_KEY] = PersistentMapping(head)

    # ------------------------------------------------------------------
    # retention primitives
    # ------------------------------------------------------------------
    def _load_policy(self) -> dict[str, Any] | None:
        value = IAnnotations(self.context).get(POLICY_KEY)
        return dict(value) if value else None

    def _store_policy(self, policy: RetentionPolicy) -> None:
        IAnnotations(self.context)[POLICY_KEY] = PersistentMapping(
            {
                "enabled": policy.enabled,
                "older_than_days": policy.older_than_days,
                "max_entries": policy.max_entries,
            }
        )

    def _previews(self, create: bool = False) -> PersistentMapping | None:
        annotations = IAnnotations(self.context)
        previews = annotations.get(PREVIEW_KEY)
        if isinstance(previews, PersistentMapping):
            return previews
        if not create:
            return None
        previews = PersistentMapping()
        annotations[PREVIEW_KEY] = previews
        return previews

    def _store_preview(self, preview: DeletionPreview) -> None:
        previews = self._previews(create=True)
        if previews is None:  # pragma: no cover - defensive annotation guard
            raise RuntimeError("preview annotation store could not be created")
        previews[str(preview.operation_id)] = preview
        previews._p_changed = True

    def cleanup_expired_previews(self, now: datetime | None = None) -> int:
        """Delete expired previews belonging to this object's annotations."""
        previews = self._previews()
        if previews is None:
            return 0
        current = now or datetime.now(UTC)
        expired = [
            operation_id
            for operation_id, preview in list(previews.items())
            if isinstance(preview, DeletionPreview) and preview.is_expired(current)
        ]
        for operation_id in expired:
            del previews[operation_id]
        if expired:
            previews._p_changed = True
        return len(expired)

    def _consume_preview(self, preview: DeletionPreview) -> DeletionPreview | None:
        previews = self._previews()
        if previews is None:
            return None
        stored = previews.get(str(preview.operation_id))
        if stored != preview:
            return None
        del previews[str(preview.operation_id)]
        previews._p_changed = True
        return stored

    def _load_preview(self, operation_id: str) -> DeletionPreview | None:
        previews = self._previews()
        if previews is None:
            return None
        value = previews.get(operation_id)
        return value if isinstance(value, DeletionPreview) else None
