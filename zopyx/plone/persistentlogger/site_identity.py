"""Stable identity helpers for site-scoped runtime caches."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast


def stable_site_key(site: Any) -> tuple[str, ...]:
    """Return an identity that survives acquisition wrappers and requests.

    Physical paths are the preferred identity for Plone sites.  UUID and name
    fallbacks keep lightweight test/during-startup site objects usable without
    ever depending on the process-local ``id()`` of a wrapper.
    """
    if site is None:
        return ("none",)
    get_physical_path = getattr(site, "getPhysicalPath", None)
    if callable(get_physical_path):
        path = get_physical_path()
        if path:
            path_parts = cast(Iterable[Any], path)
            return ("path", *(str(part) for part in path_parts))
    try:
        from plone.uuid.interfaces import IUUID

        uid = IUUID(site, None)
    except Exception:
        uid = None
    if uid:
        return ("uuid", str(uid))
    name = getattr(site, "__name__", None)
    if name is not None:
        return ("name", str(name))
    return ("class", type(site).__module__, type(site).__qualname__)


__all__ = ["stable_site_key"]
