"""Object-scoped retention and deletion orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import DeletionPreview, DeletionResult, RetentionPolicy, utc_now
from .repository import AnnotationRepository


class RetentionService:
    """Apply a retention policy to one Plone object."""

    def __init__(self, context: Any, repository: AnnotationRepository | None = None):
        self.repository = repository or AnnotationRepository(context)

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
