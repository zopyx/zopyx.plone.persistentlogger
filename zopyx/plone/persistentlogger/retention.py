"""Object-scoped retention and deletion orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import DeletionPreview, DeletionResult, RetentionPolicy, utc_now
from .storage import LogRepository, get_repository


class RetentionExecutionError(RuntimeError):
    """Report a rolled-back retention operation without governance evidence."""

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
        try:
            return self.repository.delete_and_journal(preview, reason, actor)
        except ValueError:
            raise
        except Exception as exc:
            # An atomic repository operation rolls back every selected event
            # when evidence cannot be committed.  Report those events as
            # failed, not deleted, so callers do not mistake an error for a
            # partially successful retention run.
            result = DeletionResult(
                preview.operation_id,
                len(preview.event_ids),
                len(preview.event_ids),
                0,
                0,
                len(preview.event_ids),
                reason,
            )
            raise RetentionExecutionError(result, exc) from exc

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
