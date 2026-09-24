"""Application-facing contracts for audit-log storage.

The concrete backends inherit :class:`BaseLogStorage`, which is an
implementation template containing shared integrity and retention behavior.
Application services should depend on this protocol instead: it describes the
repository operations they consume without coupling them to a backend or to
the template's protected persistence hooks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..models import DeletionPreview, DeletionResult, LogEvent, RetentionPolicy
from .query import Condition, ConditionGroup, SearchResult, SortSpec


@runtime_checkable
class LogRepository(Protocol):
    """Public repository contract implemented by every storage backend."""

    def object_uid(self) -> str:
        """Return the stable identifier of the object owning this log."""
        ...

    def events(self) -> list[dict[str, Any]]:
        """Return all event records in display order."""
        ...

    def event_chain_anchor(self) -> str:
        """Return the digest anchoring the current event chain."""
        ...

    def search(
        self,
        conditions: Condition | ConditionGroup | None = None,
        sort: tuple[SortSpec, ...] = (),
        offset: int = 0,
        limit: int | None = None,
        quick: str = "",
    ) -> SearchResult:
        """Return one page of records matching the query."""
        ...

    def append(self, event: LogEvent) -> dict[str, Any]:
        """Append one validated event."""
        ...

    def get(self, event_id: Any) -> dict[str, Any] | None:
        """Return one event by identifier, if it exists."""
        ...

    def last_digest(self) -> str:
        """Return the current event-chain head digest."""
        ...

    def policy(self) -> RetentionPolicy:
        """Return the retention policy for this object."""
        ...

    def set_policy(self, policy: RetentionPolicy) -> None:
        """Persist the retention policy for this object."""
        ...

    def preview_delete(
        self, policy: RetentionPolicy, now: datetime | None = None
    ) -> DeletionPreview:
        """Create and persist a server-side deletion preview."""
        ...

    def cleanup_expired_previews(self, now: datetime | None = None) -> int:
        """Delete expired previews and return the number removed."""
        ...

    def remove_object(self) -> int:
        """Remove lifecycle data owned by the object."""
        ...

    def delete_preview(
        self,
        preview: DeletionPreview,
        reason: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Consume a preview and delete its selected events."""
        ...

    def delete_and_journal(
        self,
        preview: DeletionPreview,
        reason: str,
        actor: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Delete a preview and persist its governance evidence atomically."""
        ...

    def get_preview(
        self, operation_id: Any, now: datetime | None = None
    ) -> DeletionPreview | None:
        """Return an unexpired deletion preview, if present."""
        ...

    def journal(self) -> list[dict[str, Any]]:
        """Return governance records in chronological order."""
        ...

    def record_governance(
        self, action: str, actor: str, reason: str, **data: object
    ) -> dict[str, Any]:
        """Append one governance record."""
        ...

    def retention_lock(self) -> Any:
        """Return a context manager serializing hold and retention changes."""
        ...
