"""Object-scoped retention and deletion orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import DeletionPreview, DeletionResult, RetentionPolicy, utc_now
from .storage import LogRepository, get_repository


class RetentionExecutionError(RuntimeError):
    """Report a deletion completed without a governance journal entry.

    The current storage contract exposes deletion and journaling as separate
    operations. Callers must treat this as an indeterminate governance state,
    not as a successful retention operation; ``result`` records what deletion
    returned before the journal failure.
    """

    def __init__(self, result: DeletionResult, cause: Exception):
        self.result = result
        super().__init__(f"retention deletion was not journaled: {cause}")


class RetentionService:
    """Apply a retention policy to one Plone object."""

    def __init__(self, context: Any, repository: LogRepository | None = None):
        self.repository = repository or get_repository(context)

    def preview(
        self, policy: RetentionPolicy, now: datetime | None = None
    ) -> DeletionPreview:
        if not policy.enabled:
            raise ValueError("retention policy is disabled")
        return self.repository.preview_delete(policy, now or utc_now())

    def execute(
        self, preview: DeletionPreview, reason: str, actor: str
    ) -> DeletionResult:
        result = self.repository.delete_preview(preview, reason)
        try:
            self.repository.record_governance(
                "retention_delete",
                actor,
                reason,
                operation_id=str(result.operation_id),
                requested=result.requested,
                eligible=result.eligible,
                deleted=result.deleted,
                missing=result.missing,
                failed=result.failed,
            )
        except Exception as exc:
            raise RetentionExecutionError(result, exc) from exc
        return result

    def set_policy(self, policy: RetentionPolicy, actor: str, reason: str) -> None:
        self.repository.set_policy(policy)
        self.repository.record_governance(
            "retention_policy_changed",
            actor,
            reason,
            enabled=policy.enabled,
            older_than_days=policy.older_than_days,
            max_entries=policy.max_entries,
        )
