"""Permission-checked, bounded JSON endpoints for audit consumers.

The endpoint is intentionally a browser view instead of an optional
``plone.restapi`` resource.  It is therefore available in the base package and
keeps the same permission and CSRF primitives as the existing views.  The
object endpoint is read-only; hold changes are explicit POST requests.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any
from uuid import UUID

import plone.api
from plone.protect import CheckAuthenticator
from Products.Five.browser import BrowserView

from ..data_subject import (
    DataSubjectQuery,
    QuotaExceeded,
    create_hold,
    export_journal,
    release_hold,
    search_data_subject,
)
from ..serialization import json_default
from ..storage import get_repository, object_uid

OBJECT_QUOTA = 1_000
SITE_QUOTA = 10_000
JOURNAL_QUOTA = 10_000
MAX_SELECTOR_LENGTH = 2_048
MAX_HOLD_EVENT_IDS = 1_000
_SAFE_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,255}\Z")


def _value(request: Any, name: str, default: Any = None) -> Any:
    form = getattr(request, "form", {})
    result = form.get(name, default)
    if result is None:
        result = request.get(name, default)
    if isinstance(result, (list, tuple)):
        raise ValueError(f"{name} must be supplied once")
    return result


def _error(request: Any, status: int, code: str, message: str) -> str:
    response = request.response
    response.setStatus(status)
    response.setHeader("Content-Type", "application/json")
    return json.dumps({"error": {"code": code, "message": message}})


def _actor() -> str:
    try:
        return str(plone.api.user.get_current().getUserName())
    except Exception:
        return ""


def _allowed(context: Any, permission: str) -> bool:
    membership = getattr(
        getattr(context, "portal_membership", None), "checkPermission", None
    )
    if not callable(membership):
        return False
    try:
        return bool(membership(permission, context))
    except Exception:
        # A permission lookup failure must not turn a protected endpoint into
        # an accidentally public one.
        return False


def _query(request: Any) -> DataSubjectQuery:
    def optional(name: str) -> str | None:
        value = _value(request, name)
        if value is None or value == "":
            return None
        value = str(value).strip()
        if not value or len(value) > MAX_SELECTOR_LENGTH:
            raise ValueError(f"{name} is invalid")
        if name == "event_id" and not _SAFE_EVENT_ID.fullmatch(value):
            raise ValueError("event_id is invalid")
        return value

    return DataSubjectQuery(
        actor=optional("actor"),
        target=optional("target"),
        event_id=optional("event_id"),
    )


def _limit(request: Any, maximum: int, *, default: int = 100) -> int:
    raw = _value(request, "limit", default)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError("limit must be an integer")
    text = str(raw).strip()
    if not text.isdecimal():
        raise ValueError("limit must be an integer")
    result = int(text)
    if not 0 < result <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return result


def _hold_id(request: Any) -> str:
    value = _value(request, "hold_id", "")
    if not isinstance(value, str) or len(value) > 255:
        raise ValueError("hold_id is invalid")
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("hold_id is invalid") from exc


class AuditAPI(BrowserView):
    """Read-only object-scoped audit search with object/site quotas."""

    permission = "zopyx.plone.persistentlogger.ViewAuditLog"

    def __call__(self) -> str:
        if self.request.method != "GET":
            return _error(self.request, 405, "method_not_allowed", "GET required")
        if not _allowed(self.context, self.permission):
            return _error(
                self.request, 403, "forbidden", "audit log access is not allowed"
            )
        try:
            limit = _limit(self.request, OBJECT_QUOTA)
            offset_raw = _value(self.request, "offset", 0)
            if isinstance(offset_raw, bool) or not isinstance(offset_raw, (int, str)):
                raise ValueError("offset must be an integer")
            offset_text = str(offset_raw).strip()
            if not offset_text.isdecimal():
                raise ValueError("offset must be an integer")
            offset = int(offset_text)
            if offset < 0 or offset + limit > OBJECT_QUOTA:
                raise ValueError(
                    f"offset and limit must fit within the {OBJECT_QUOTA}-record "
                    "object quota"
                )
            result = search_data_subject(
                [self.context],
                _query(self.request),
                limit=offset + limit,
                site_limit=SITE_QUOTA,
            )
        except QuotaExceeded as exc:
            return _error(self.request, 413, "quota_exceeded", str(exc))
        except ValueError as exc:
            return _error(self.request, 400, "invalid_query", str(exc))
        except Exception:
            return _error(
                self.request,
                500,
                "internal_error",
                "audit search could not be completed",
            )
        rows = list(result.rows)[offset : offset + limit]
        # Do not persist selector values: actor/target can themselves be
        # personal data.  The journal records only bounded operation metadata.
        try:
            get_repository(self.context).record_governance(
                "audit_log_access",
                _actor(),
                "read-only audit API access",
                object_uid=object_uid(self.context),
                result_count=len(rows),
            )
        except Exception:
            return _error(
                self.request,
                500,
                "internal_error",
                "audit access could not be recorded",
            )
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps(
            {
                "schema_version": 1,
                "object_uid": object_uid(self.context),
                "offset": offset,
                "limit": limit,
                "object_quota": OBJECT_QUOTA,
                "site_quota": SITE_QUOTA,
                "total": result.total,
                "records": rows,
            },
            default=json_default,
            ensure_ascii=False,
        )


class AuditExportAPI(BrowserView):
    """Export a bounded governance journal as JSON."""

    permission = "zopyx.plone.persistentlogger.ExportAuditLog"

    def __call__(self) -> bytes | str:
        if self.request.method != "GET":
            return _error(self.request, 405, "method_not_allowed", "GET required")
        if not _allowed(self.context, self.permission):
            return _error(self.request, 403, "forbidden", "audit export is not allowed")
        try:
            limit = _limit(self.request, JOURNAL_QUOTA, default=JOURNAL_QUOTA)
            data = export_journal([self.context], limit=limit)
        except QuotaExceeded as exc:
            return _error(self.request, 413, "quota_exceeded", str(exc))
        except ValueError as exc:
            return _error(self.request, 400, "invalid_query", str(exc))
        except Exception:
            return _error(
                self.request, 500, "internal_error", "audit export could not be created"
            )
        self.request.response.setHeader("Content-Type", "application/json")
        self.request.response.setHeader(
            "Content-Disposition", 'attachment; filename="audit-journal.json"'
        )
        return data


class HoldAPI(BrowserView):
    """Create or release legal holds using POST + Plone CSRF protection."""

    permission = "zopyx.plone.persistentlogger.ManageAuditHold"

    def __call__(self) -> str:
        if self.request.method != "POST":
            return _error(self.request, 405, "method_not_allowed", "POST required")
        if not _allowed(self.context, self.permission):
            return _error(
                self.request,
                403,
                "forbidden",
                "legal hold management is not allowed",
            )
        CheckAuthenticator(self.request)
        try:
            action = str(_value(self.request, "action", "create"))
            if action == "create":
                event_ids_raw = str(_value(self.request, "event_ids", ""))
                reason = str(_value(self.request, "reason", ""))
                if len(event_ids_raw) > MAX_HOLD_EVENT_IDS * 256:
                    raise ValueError("event_ids is too large")
                if len(reason) > MAX_SELECTOR_LENGTH:
                    raise ValueError("reason is too large")
                event_ids = tuple(
                    item.strip() for item in event_ids_raw.split(",") if item.strip()
                )
                if len(event_ids) > MAX_HOLD_EVENT_IDS or any(
                    not _SAFE_EVENT_ID.fullmatch(item) for item in event_ids
                ):
                    raise ValueError("event_ids contains an invalid identifier")
                hold = create_hold(
                    self.context,
                    _actor(),
                    reason,
                    event_ids=event_ids,
                )
                payload: dict[str, Any] = asdict(hold)
            elif action == "release":
                reason = str(_value(self.request, "reason", ""))
                if len(reason) > MAX_SELECTOR_LENGTH:
                    raise ValueError("reason is too large")
                hold = release_hold(
                    self.context,
                    _hold_id(self.request),
                    _actor(),
                    reason,
                )
                payload = asdict(hold)
            else:
                return _error(
                    self.request, 400, "invalid_action", "unknown hold action"
                )
        except ValueError as exc:
            return _error(self.request, 400, "invalid_hold", str(exc))
        except Exception:
            return _error(
                self.request, 500, "internal_error", "legal hold operation failed"
            )
        self.request.response.setHeader("Content-Type", "application/json")
        return json.dumps(payload, default=json_default, ensure_ascii=False)


__all__ = ["AuditAPI", "AuditExportAPI", "HoldAPI"]
