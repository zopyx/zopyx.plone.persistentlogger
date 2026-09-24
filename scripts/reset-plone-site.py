"""Reset the local Plone site and install this add-on.

Executed by ``zconsole run`` with the Zope application bound as ``app``.
This script is intended for local development only.
"""

# ``app`` is injected by ``zconsole run``.
# ruff: noqa: F821

from Products.CMFPlone.factory import addPloneSite
from transaction import commit

site_id = "Plone"
if site_id in app.objectIds():
    app.manage_delObjects([site_id])
    commit()

portal = addPloneSite(
    app,
    site_id,
    title="Plone",
    profile_id="Products.CMFPlone:plone",
    # Mirror Products.CMFPlone.browser.admin.AddPloneSite.default_extension_profiles:
    # the base profile has no dependencies, so the theme and the default
    # content types must be installed explicitly.
    extension_ids=(
        "plone.app.contenttypes:default",
        # The theme layer: without it the Diazo transform and the theme CSS are
        # not applied, even with a theme selected in the registry.
        "plone.app.theming:default",
        "plonetheme.barceloneta:default",
    ),
    default_language="de",
    portal_timezone="Europe/Berlin",
)
portal.portal_setup.runAllImportStepsFromProfile(
    "profile-zopyx.plone.persistentlogger:default"
)
commit()
print(f"Created fresh Plone site /{site_id} with zopyx.plone.persistentlogger")
