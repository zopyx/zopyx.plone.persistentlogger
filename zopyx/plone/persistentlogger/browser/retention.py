"""Manager-only browser views for retention and export operations."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

import plone.api
from plone.protect import CheckAuthenticator
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from ..exports import export_events
from ..models import DeletionPreview, ExportRequest, RetentionPolicy
from ..retention import RetentionService
from ..serialization import json_default
from ..storage import BaseLogStorage, get_repository

MAX_EXPORT_ENTRIES = 100_000


def _request_value(request, name: str, default: object = None) -> object:
    """Read a value from either a form body or the query string."""
    form = getattr(request, "form", {})
    value = form.get(name, default)
    if value is None:
        getter = getattr(request, "get", None)
        if getter is not None:
            value = getter(name, default)
    return value


def _error_response(request, status: int, code: str, message: str) -> str:
    """Return a small, safe JSON error response for browser callers."""
    response = getattr(request, "response", None)
    if response is not None:
        response.setStatus(status)
        response.setHeader("Content-Type", "application/json")
    return json.dumps({"error": {"code": code, "message": message}})


def _integer(value: object, field: str) -> int:
    """Parse a strict decimal form value."""
    if value is None or value == "":
        raise ValueError(f"{field} is required")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc


class Retention(BrowserView):
    """Preview and execute object-scoped retention operations."""

    def preview(self) -> str:
        if self.request.method != "POST":
            return _error_response(
                self.request, 405, "method_not_allowed", "POST required"
            )
        CheckAuthenticator(self.request)
        try:
            raw_older = _request_value(self.request, "older_than_days")
            raw_max = _request_value(self.request, "max_entries")
            parsed_older = (
                None if raw_older is None else _integer(raw_older, "older_than_days")
            )
            parsed_max = None if raw_max is None else _integer(raw_max, "max_entries")
        except ValueError as exc:
            return _error_response(self.request, 400, "invalid_number", str(exc))
        repository = get_repository(self.context)
        try:
            configured = repository.policy()
            policy = RetentionPolicy(
                enabled=configured.enabled,
                older_than_days=(
                    configured.older_than_days if parsed_older is None else parsed_older
                ),
                max_entries=(
                    configured.max_entries if parsed_max is None else parsed_max
                ),
            )
        except ValueError as exc:
            code = "invalid_number" if "integer" in str(exc) else "invalid_policy"
            return _error_response(self.request, 400, code, str(exc))
        preview = RetentionService(self.context, repository).preview(policy)
        return json.dumps(asdict(preview), default=json_default, ensure_ascii=False)

    def delete(self) -> str:
        if self.request.method != "POST":
            self.request.response.setStatus(405)
            return "POST required"
        CheckAuthenticator(self.request)
        try:
            operation_id = UUID(str(self.request.form.get("operation_id", "")))
        except (TypeError, ValueError, AttributeError):
            return _error_response(
                self.request, 400, "invalid_uuid", "operation_id must be a valid UUID"
            )
        repository = get_repository(self.context)
        preview = repository.get_preview(operation_id)
        if preview is None:
            self.request.response.setStatus(400)
            return "deletion preview is missing or stale"
        actor = plone.api.user.get_current().getUserName()
        try:
            result = RetentionService(self.context, repository).execute(
                preview, str(self.request.form.get("reason", "")), actor
            )
        except ValueError as exc:
            return _error_response(self.request, 400, "invalid_reason", str(exc))
        return json.dumps(asdict(result), default=json_default)


class Export(BrowserView):
    """Return one object log in a selected supported format."""

    def __call__(self) -> bytes | str:
        format_name = str(_request_value(self.request, "format", "json"))
        try:
            ExportRequest(format_name)
        except ValueError as exc:
            return _error_response(self.request, 400, "invalid_export", str(exc))
        repository = get_repository(self.context)
        result = repository.search(limit=MAX_EXPORT_ENTRIES + 1)
        if result.total > MAX_EXPORT_ENTRIES:
            return _error_response(
                self.request,
                413,
                "export_limit",
                "export exceeds the configured entry limit",
            )
        try:
            data = export_events(list(result.rows), format_name)
        except ValueError as exc:
            return _error_response(self.request, 400, "invalid_export", str(exc))
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
    def repository(self) -> BaseLogStorage:
        return get_repository(self.context)

    @property
    def policy(self) -> RetentionPolicy:
        return self.repository.policy()

    @property
    def preview(self) -> DeletionPreview | None:
        operation_id = self.request.form.get("operation_id")
        if not operation_id:
            return None
        return self.repository.get_preview(operation_id)

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
