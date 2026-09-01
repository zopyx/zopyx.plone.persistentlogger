"""Manager-only browser views for retention and export operations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any
from uuid import UUID

import plone.api
from plone.protect import CheckAuthenticator
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.annotation.interfaces import IAnnotations

from ..exports import export_events
from ..models import DeletionPreview, RetentionPolicy
from ..repository import PREVIEW_KEY, AnnotationRepository
from ..retention import RetentionService
from ..serialization import json_default


class Retention(BrowserView):
    """Preview and execute object-scoped retention operations."""

    def preview(self) -> str:
        repository = AnnotationRepository(self.context)
        configured = repository.policy()
        policy = RetentionPolicy(
            enabled=configured.enabled,
            older_than_days=int(
                self.request.form.get("older_than_days", configured.older_than_days)
            ),
            max_entries=int(
                self.request.form.get("max_entries", configured.max_entries)
            ),
        )
        preview = RetentionService(self.context, repository).preview(policy)
        return json.dumps(asdict(preview), default=json_default, ensure_ascii=False)

    def delete(self) -> str:
        if self.request.method != "POST":
            self.request.response.setStatus(405)
            return "POST required"
        CheckAuthenticator(self.request)
        operation_id = UUID(str(self.request.form["operation_id"]))
        repository = AnnotationRepository(self.context)
        previews = IAnnotations(self.context).get(
            "zopyx.plone.persistentlogger.connector.previews"
        )
        if previews is None:
            previews = {}
        preview = previews.get(str(operation_id))
        if preview is None:
            self.request.response.setStatus(400)
            return "deletion preview is missing or stale"
        actor = plone.api.user.get_current().getUserName()
        result = RetentionService(self.context, repository).execute(
            preview, str(self.request.form.get("reason", "")), actor
        )
        return json.dumps(asdict(result), default=json_default)


class Export(BrowserView):
    """Return one object log in a selected supported format."""

    def __call__(self) -> bytes:
        format_name = str(self.request.form.get("format", "json"))
        data = export_events(
            list(AnnotationRepository(self.context).events()), format_name
        )
        content_types = {
            "json": "application/json",
            "csv": "text/csv; charset=utf-8",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ods": "application/vnd.oasis.opendocument.spreadsheet",
        }
        self.request.response.setHeader("Content-Type", content_types[format_name])
        self.request.response.setHeader(
            "Content-Disposition",
            f'attachment; filename="persistent-log.{format_name}"',
        )
        return data


class RetentionGUI(BrowserView):
    """HTML management page for the object-scoped retention workflow."""

    template = ViewPageTemplateFile("retention.pt")

    def __init__(self, context, request):
        super().__init__(context, request)
        self.messages: list[tuple[str, str]] = []

    @property
    def repository(self) -> AnnotationRepository:
        return AnnotationRepository(self.context)

    @property
    def policy(self) -> RetentionPolicy:
        return self.repository.policy()

    @property
    def preview(self) -> DeletionPreview | None:
        operation_id = self.request.form.get("operation_id")
        if not operation_id:
            return None
        previews = IAnnotations(self.context).get(PREVIEW_KEY)
        if not isinstance(previews, Mapping):
            return None
        value = previews.get(str(operation_id))
        return value if isinstance(value, DeletionPreview) else None

    @property
    def preview_events(self) -> list[dict[str, Any]]:
        preview = self.preview
        if preview is None:
            return []
        events = []
        for event_id in preview.event_ids:
            entry = self.repository.get(str(event_id))
            if entry is not None:
                events.append(entry)
        return events

    def _policy_from_form(self) -> RetentionPolicy:
        """Policy used for previews: stored enabled flag, form overrides limits."""
        form = self.request.form
        current = self.policy
        return RetentionPolicy(
            enabled=current.enabled,
            older_than_days=int(form.get("older_than_days", current.older_than_days)),
            max_entries=int(form.get("max_entries", current.max_entries)),
        )

    def __call__(self):
        form = self.request.form
        if self.request.method == "POST":
            CheckAuthenticator(self.request)
            action = form.get("action")
            try:
                if action == "save-policy":
                    self._save_policy(form)
                elif action == "preview":
                    self._make_preview(form)
                elif action == "delete":
                    self._delete(form)
            except ValueError as exc:
                self.messages.append(("error", str(exc)))
        return self.template()

    def _save_policy(self, form) -> None:
        current = self.policy
        policy = RetentionPolicy(
            enabled=str(form.get("enabled", "")) == "1",
            older_than_days=int(form.get("older_than_days", current.older_than_days)),
            max_entries=int(form.get("max_entries", current.max_entries)),
        )
        actor = plone.api.user.get_current().getUserName()
        RetentionService(self.context, self.repository).set_policy(
            policy, actor, "retention policy updated via management GUI"
        )
        self.messages.append(("info", "Retention policy saved."))

    def _make_preview(self, form) -> None:
        preview = RetentionService(self.context, self.repository).preview(
            self._policy_from_form()
        )
        form["operation_id"] = str(preview.operation_id)
        self.messages.append(
            ("info", f"{len(preview.event_ids)} entries are eligible for deletion.")
        )

    def _delete(self, form) -> None:
        preview = self.preview
        if preview is None:
            raise ValueError("deletion preview is missing or stale")
        actor = plone.api.user.get_current().getUserName()
        result = RetentionService(self.context, self.repository).execute(
            preview, str(form.get("reason", "")), actor
        )
        form["operation_id"] = ""
        self.messages.append(
            ("info", f"Deleted {result.deleted} entries ({result.missing} missing).")
        )
