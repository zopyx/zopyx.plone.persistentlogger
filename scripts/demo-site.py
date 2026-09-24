"""Set up the local demo site and fill the Plone root object with demo entries.

Executed by ``zconsole run`` with the Zope application bound as ``app``, driven
by ``make demo``.  The script is idempotent in the sense that it reuses an
existing Plone site and *appends* another batch of demo entries, so it can be
run repeatedly while developing.  Local development only: it creates a site,
installs profiles and writes fake audit records.

Set ``DEMO_ENTRIES`` to change the number of entries written (default 250).
"""

from __future__ import annotations

import os
import random
from datetime import timedelta
from typing import Any

from plone.app.theming.interfaces import IThemeSettings
from plone.app.theming.utils import applyTheme, getAvailableThemes, getTheme
from Products.CMFPlone.factory import addPloneSite
from transaction import commit
from zope.component.hooks import setSite

from zopyx.plone.persistentlogger.models import LogEvent, Severity, utc_now
from zopyx.plone.persistentlogger.storage import get_repository

SITE_ID = "Plone"
PROFILE = "zopyx.plone.persistentlogger"
#: Theme the demo site uses; ``plonetheme.barceloneta`` ships with Plone 6.
THEME_PROFILE = "plonetheme.barceloneta:default"
THEME_NAME = "barceloneta"
#: ``plonetheme.barceloneta:default`` depends on it, but the layered page
#: rendering (Diazo transform and theme CSS) only happens with the theming
#: browser layer installed, so the profile is named explicitly.
THEMING_PROFILE = "plone.app.theming:default"
#: Plone ships these in ``IThemeSettings.hostnameBlacklist`` ("Unthemed host
#: names"), which switches the theme *off* for exactly the URL the demo is
#: served from -- so the demo removes them.
UNTHEMED_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})
DEFAULT_ENTRIES = 250

#: Demo data is spread over this window so retention previews and date
#: filters have something to work with.
SPREAD_DAYS = 540

ACTORS = ("admin", "editor", "reviewer", "auditor", "system")
EVENT_TYPES = (
    "application",
    "governance",
    "workflow",
    "export",
    "retention",
    "security",
)
SEVERITIES = (
    Severity.DEBUG,
    Severity.INFO,
    Severity.INFO,
    Severity.INFO,
    Severity.WARNING,
    Severity.ERROR,
    Severity.CRITICAL,
)
TARGETS = (
    "content",
    "workflow",
    "permissions",
    "retention policy",
    "export",
    "configuration",
)
COMMENTS = (
    "Content published",
    "Workflow transition executed",
    "Metadata updated",
    "Review comment added",
    "Retention policy inspected",
    "Export requested",
    "Permission changed",
    "Configuration changed",
    "Attachment uploaded",
    "Scheduled publication set",
    "Language changed",
    "Review state reset",
    "Security check performed",
    "Integrity chain verified",
)
DETAIL_KEYS = (
    "workflow",
    "revision",
    "field",
    "old",
    "new",
    "size",
    "duration_ms",
    "source",
)


def find_site(app: Any) -> Any | None:
    """Return the existing Plone site, if there is one."""
    for obj in app.objectValues():
        if hasattr(obj, "portal_setup"):
            return obj
    return None


def ensure_site(app: Any) -> tuple[Any, bool]:
    """Return the Plone site, creating it when it does not exist yet."""
    site = find_site(app)
    if site is not None:
        return site, False
    site = addPloneSite(
        app,
        SITE_ID,
        title="Plone",
        profile_id="Products.CMFPlone:plone",
        # Mirror Products.CMFPlone.browser.admin.AddPloneSite: the base
        # profile has no dependencies, so theme and content types are
        # installed explicitly.
        extension_ids=(
            "plone.app.contenttypes:default",
            "plonetheme.barceloneta:default",
        ),
        default_language="de",
        portal_timezone="Europe/Berlin",
    )
    commit()
    return site, True


def install_profiles(site: Any) -> None:
    """Install the theme, the add-on profile and the demo profile.

    ``plone.app.theming`` and ``plonetheme.barceloneta`` are installed
    explicitly (and idempotently), so a demo site that was created without
    them -- or a site whose theme was switched away -- ends up with the
    standard Plone theme again.
    """
    for profile in (
        f"profile-{THEMING_PROFILE}",
        f"profile-{THEME_PROFILE}",
        f"profile-{PROFILE}:default",
        f"profile-{PROFILE}:demo",
    ):
        site.portal_setup.runAllImportStepsFromProfile(profile)
    commit()


def activate_theme(site: Any, name: str = THEME_NAME) -> tuple[str, list[str]]:
    """Activate the theme *and* make sure it is not blacklisted.

    ``applyTheme`` is what the theming control panel calls: it writes the
    theme name, rules, prefix and doctype into the registry and switches
    theming on.  That alone is not enough for the demo instance, which is
    served from ``http://127.0.0.1:8080``: Plone ships ``127.0.0.1`` and
    ``localhost`` in ``hostnameBlacklist`` ("Unthemed host names") and
    ``ThemingPolicy.isThemeEnabled`` then refuses to transform those hosts at
    all -- the registry looks right while the page stays unthemed.

    Returns the theme name and the host names that are still untethered.
    """
    available = {theme.__name__ for theme in getAvailableThemes()}
    if name not in available:
        raise RuntimeError(
            f"theme {name!r} is not available; installed themes: {sorted(available)}"
        )
    applyTheme(getTheme(name))

    settings = site.portal_registry.forInterface(IThemeSettings, False)
    if settings is None:  # pragma: no cover - defensive
        raise RuntimeError("the theming settings are not registered")
    settings.enabled = True
    blacklist = {host.strip() for host in settings.hostnameBlacklist or ()}
    settings.hostnameBlacklist = sorted(blacklist - UNTHEMED_HOSTNAMES)
    site.portal_registry._p_changed = True
    commit()
    return name, sorted(settings.hostnameBlacklist)


def make_details(index: int) -> dict[str, Any]:
    """Return a small, JSON-safe details payload for one entry."""
    keys = random.sample(DETAIL_KEYS, random.randint(1, 3))
    details: dict[str, Any] = {"source": "demo", "demo_index": index}
    for key in keys:
        if key in {"revision", "size", "duration_ms"}:
            details[key] = random.randint(1, 10_000)
        elif key == "workflow":
            details[key] = random.choice(("published", "pending", "private"))
        else:
            details[key] = f"{key}-{random.randint(1, 999)}"
    return details


def demo_events(count: int) -> list[LogEvent]:
    """Build ``count`` demo events, oldest first."""
    now = utc_now()
    events = [
        LogEvent(
            comment=random.choice(COMMENTS),
            created_at=now - timedelta(minutes=random.randint(1, SPREAD_DAYS * 1440)),
            severity=random.choice(SEVERITIES),
            actor=random.choice(ACTORS),
            event_type=random.choice(EVENT_TYPES),
            target=random.choice(TARGETS),
            info_url=f"/Plone/@@persistent-log?demo={index}"
            if index % 5 == 0
            else None,
            details=make_details(index),
        )
        for index in range(count)
    ]
    # The integrity chain is built in chronological order.
    events.sort(key=lambda event: event.created_at)
    return events


def main() -> None:
    random.seed(os.environ.get("DEMO_SEED", "zopyx"))
    count = int(os.environ.get("DEMO_ENTRIES", DEFAULT_ENTRIES))

    site, created = ensure_site(app)  # noqa: F821 - bound by zconsole
    print(f"{'created' if created else 'reusing'} Plone site /{site.getId()}")
    # GenericSetup import steps and the theming handler resolve utilities
    # (the registry in particular) through the local site manager, so the site
    # has to be the current site while the profiles are installed.
    setSite(site)
    install_profiles(site)
    theme, unthemed = activate_theme(site)
    print(f"theme: {theme} ({THEME_PROFILE}), enabled")
    print(f"unthemed host names: {unthemed or 'none'}")

    repository = get_repository(site)
    before = len(repository.events())
    for event in demo_events(count):
        repository.append(event)

    repository.record_governance(
        "demo_data_generated",
        "admin",
        f"created {count} demo entries on the Plone root object",
        entries=count,
        backend=type(repository).__name__,
    )
    commit()

    total = len(repository.events())
    print(f"wrote {count} demo entries ({before} before, {total} now)")
    print(f"backend: {type(repository).__name__}")
    print(f"theme: {THEME_NAME} ({THEME_PROFILE})")
    print(f"open http://127.0.0.1:8080/{site.getId()}/@@persistent-log")
    print("start the server with `make dev`")


main()
