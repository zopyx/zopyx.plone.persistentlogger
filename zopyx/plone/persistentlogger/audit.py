"""Event subscribers for site-wide audit logging.

Creation and modification events are recorded in the object-local
persistent log as ``create`` and ``edit`` entries. Metadata changes are
stored as a per-field diff (old/new values) inside the entry details. The
diff is computed against a persistent metadata snapshot taken when the
object was created; objects that already existed before the feature was
enabled get a baseline snapshot on their first modification without an
audit entry.
"""

from __future__ import annotations

from typing import Any

import plone.api
from plone.registry.interfaces import IRegistry
from zope.annotation.interfaces import IAnnotations
from zope.component import getUtility
from zope.component.hooks import getSite
from zope.interface import implementer

from zopyx.plone.persistentlogger.api import log_event
from zopyx.plone.persistentlogger.interfaces import IAuditLoggingSettings

SNAPSHOT_KEY = "zopyx.plone.persistentlogger.connector.audit.snapshot"

_METADATA_FIELDS = (
    "title",
    "description",
    "subject",
    "language",
    "effective_date",
    "expiration_date",
    "creators",
    "id",
    "portal_type",
    "uid",
)

# Registry lookups are expensive on every content event; cache the
# resolved settings proxy per site and invalidate on record changes.
_settings_cache: dict[int, Any] = {}


@implementer(IAuditLoggingSettings)
class _Settings:
    """Fallback settings when the registry is not installed yet."""

    enabled = False
    content_types = []


def audit_settings() -> Any:
    """Return the registry settings or a disabled fallback (cached)."""
    site_key = id(getSite())
    cached = _settings_cache.get(site_key)
    if cached is not None:
        return cached
    try:
        registry = getUtility(IRegistry)
        settings = registry.forInterface(IAuditLoggingSettings, check=False)
    except Exception:
        return _Settings()
    _settings_cache[site_key] = settings
    return settings


def audit_settings_changed(record: Any = None, event: Any = None) -> None:
    """Drop the cached settings when registry records change."""
    _settings_cache.clear()


def is_audited(obj: Any) -> bool:
    """Return whether audit logging applies to this object."""
    settings = audit_settings()
    if not settings.enabled:
        return False
    portal_type = getattr(obj, "portal_type", None)
    content_types = tuple(settings.content_types or ())
    if not content_types:
        return True
    return portal_type in content_types


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _metadata(obj: Any) -> dict[str, Any]:
    def getter(name: str) -> Any:
        candidates = {
            "title": ("Title", "title"),
            "description": ("Description", "description"),
            "subject": ("Subject", "subject"),
            "language": ("Language", "language"),
            "effective_date": ("effective", "effective_date", "effectiveDate"),
            "expiration_date": ("expires", "expiration_date", "expirationDate"),
            "creators": ("Creators", "creators"),
            "id": ("getId", "id"),
            "portal_type": ("portal_type",),
            "uid": ("UID",),
        }
        for attr in candidates[name]:
            value = getattr(obj, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
            if value is not None:
                return value
        return None

    return {name: _jsonable(getter(name)) for name in _METADATA_FIELDS}


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changes[key] = {"old": old.get(key), "new": new.get(key)}
    return changes


def _actor() -> str:
    try:
        return plone.api.user.get_current().getUserName()
    except Exception:
        return ""


def audit_object_created(obj: Any, event: Any) -> None:
    if not is_audited(obj):
        return
    annotations = IAnnotations(obj)
    annotations[SNAPSHOT_KEY] = _metadata(obj)
    annotations._p_changed = True
    log_event(
        obj,
        "Content created",
        level="info",
        actor=_actor(),
        event_type="create",
        details=annotations[SNAPSHOT_KEY],
    )


def audit_object_modified(obj: Any, event: Any) -> None:
    if not is_audited(obj):
        return
    annotations = IAnnotations(obj)
    snapshot = annotations.get(SNAPSHOT_KEY)
    current = _metadata(obj)
    if not isinstance(snapshot, dict):
        # Baseline for objects created before audit logging was enabled.
        annotations[SNAPSHOT_KEY] = current
        annotations._p_changed = True
        return
    changes = _diff(snapshot, current)
    annotations[SNAPSHOT_KEY] = current
    annotations._p_changed = True
    if not changes:
        return
    log_event(
        obj,
        "Content modified",
        level="info",
        actor=_actor(),
        event_type="edit",
        details={"changes": changes, "metadata": current},
    )
