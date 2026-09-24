"""Explicit migrations for legacy annotation log records.

Migrations are intentionally not run by reads.  A GenericSetup upgrade calls
:func:`migrate_site`, while ordinary repository reads remain side-effect free.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from persistent.mapping import PersistentMapping
from zope.annotation.interfaces import IAnnotations

from ..serialization import canonical_event_date, event_digest, sanitized_details

LOG_KEY = "zopyx.plone.persistentlogger.connector.log"
QUARANTINE_KEY = "zopyx.plone.persistentlogger.connector.log-quarantine"
FALLBACK_DATE = datetime.min.replace(tzinfo=UTC)


def _quarantine(
    quarantine: MutableMapping[Any, Any] | None,
    key: Any,
    value: Any,
    reason: str,
) -> bool:
    """Copy a bad legacy value to quarantine when an upgrade supplied one."""
    if quarantine is None:
        return False
    quarantine_key = f"{uuid4()}"
    record = {
        "original_key": repr(key),
        "reason": reason,
        "record": value,
    }
    try:
        quarantine[quarantine_key] = record
    except (TypeError, ValueError):
        # Persistent mappings may reject arbitrary legacy objects.  Keeping a
        # repr still preserves evidence and, importantly, cannot abort upgrade.
        quarantine[quarantine_key] = {
            "original_key": repr(key),
            "reason": reason,
            "record": repr(value),
        }
    return True


def _normalise_entry(value: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical current-shape copy of one legacy record."""
    event_id = str(value.get("event_id") or value.get("uuid") or uuid4())
    created_at = canonical_event_date(value)
    actor = str(value.get("actor", value.get("username", "")) or "")
    event_type = str(value.get("event_type", "application") or "application")
    severity = value.get("severity", value.get("level", "info"))
    severity = str(getattr(severity, "value", severity) or "info")
    details = sanitized_details(value.get("details", value.get("details_raw")))

    entry = dict(value)
    entry.update(
        {
            "uuid": event_id,
            "event_id": event_id,
            "date": created_at,
            "created_at": created_at,
            "username": actor,
            "actor": actor,
            "level": severity,
            "severity": severity,
            "event_type": event_type,
            "details": details,
            "details_raw": details,
        }
    )
    try:
        entry["schema_version"] = int(value.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        entry["schema_version"] = 1
    entry["target"] = str(value.get("target", "") or "")
    entry["comment"] = str(value.get("comment", "") or "")
    info_url = value.get("info_url")
    entry["info_url"] = None if info_url is None else str(info_url)
    if value.get("sequence") is not None:
        try:
            sequence = int(value["sequence"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sequence") from exc
        if sequence <= 0:
            raise ValueError("invalid sequence")
        entry["sequence"] = sequence
    return entry


def migrate_store(
    store: MutableMapping[Any, Any],
    quarantine: MutableMapping[Any, Any] | None = None,
) -> int:
    """Normalize legacy entries in-place and return the number changed.

    Records are ordered by normalized UTC timestamp and then ID.  Missing or
    malformed dates use ``datetime.min`` in UTC, so one bad timestamp cannot
    make the whole migration fail.  Values that cannot be represented as a
    canonical record are quarantined when the caller provides a quarantine
    mapping; otherwise they are left untouched and skipped.
    """
    original_items = list(store.items())
    candidates: list[tuple[Any, dict[str, Any]]] = []
    preserved: list[tuple[Any, Any]] = []
    changed = 0
    for key, value in original_items:
        if not isinstance(value, dict):
            if _quarantine(quarantine, key, value, "record is not a mapping"):
                changed += 1
                del store[key]
            else:
                preserved.append((key, value))
            continue
        try:
            entry = _normalise_entry(value)
        except (TypeError, ValueError, KeyError) as exc:
            if _quarantine(quarantine, key, value, f"invalid record: {exc}"):
                changed += 1
                del store[key]
            else:
                preserved.append((key, value))
            continue
        candidates.append((key, entry))

    candidates.sort(
        key=lambda item: (
            canonical_event_date(item[1]),
            str(item[1].get("event_id", "")),
        )
    )

    seen_ids: set[str] = set()
    valid: list[tuple[Any, dict[str, Any]]] = []
    for key, entry in candidates:
        event_id = str(entry["event_id"])
        if event_id in seen_ids:
            if _quarantine(quarantine, key, entry, "duplicate event id"):
                changed += 1
                del store[key]
            else:
                preserved.append((key, entry))
            continue
        seen_ids.add(event_id)
        valid.append((key, entry))

    previous = ""
    migrated: dict[Any, Any] = dict(preserved)
    for key, entry in valid:
        entry["previous_digest"] = previous
        entry["integrity_digest"] = event_digest(entry, previous)
        migrated[str(entry["event_id"])] = entry
        previous = entry["integrity_digest"]
        original = next(value for old_key, value in original_items if old_key == key)
        if key != entry["event_id"] or entry != dict(original):
            changed += 1

    for key in list(store):
        del store[key]
    for key, entry in migrated.items():
        store[key] = entry
    if changed and hasattr(store, "_p_changed"):
        store._p_changed = True
    return changed


def migrate_annotations(annotations: MutableMapping[Any, Any]) -> int:
    """Migrate one object's annotations, keeping bad records quarantined."""
    store = annotations.get(LOG_KEY)
    if not isinstance(store, MutableMapping):
        return 0
    quarantine = annotations.get(QUARANTINE_KEY)
    if not isinstance(quarantine, MutableMapping):
        quarantine = PersistentMapping()
    changed = migrate_store(store, quarantine)
    if quarantine:
        annotations[QUARANTINE_KEY] = quarantine
    return changed


def _site_objects(site: Any) -> list[Any]:
    """Yield the site and catalog objects without requiring a live catalog."""
    objects = [site]
    catalog = getattr(site, "portal_catalog", None)
    if catalog is None:
        try:
            from Products.CMFCore.utils import getToolByName

            catalog = getToolByName(site, "portal_catalog", None)
        except Exception:
            catalog = None
    if catalog is not None:
        try:
            brains = catalog.unrestrictedSearchResults()
        except Exception:
            brains = ()
        for brain in brains:
            try:
                obj = brain._unrestrictedGetObject()
            except Exception:
                continue
            objects.append(obj)
    return objects


def migrate_site(context: Any) -> int:
    """Run the record migration for every reachable content object."""
    site = context.getSite() if hasattr(context, "getSite") else context
    total = 0
    seen: set[int] = set()
    for obj in _site_objects(site):
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        try:
            total += migrate_annotations(IAnnotations(obj))
        except Exception:
            # A single inaccessible object must not prevent the remaining site
            # from upgrading.  Its record is revisited by a later run.
            continue
    return total


def upgrade_to_3(context: Any) -> None:
    """GenericSetup handler for the version 2 -> 3 profile upgrade."""
    migrate_site(context)
