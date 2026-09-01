"""Manager-only browser views for retention and export operations."""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

import plone.api
from plone.protect import CheckAuthenticator
from Products.Five.browser import BrowserView
from zope.annotation.interfaces import IAnnotations

from ..exports import export_events
from ..models import RetentionPolicy
from ..repository import AnnotationRepository
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
