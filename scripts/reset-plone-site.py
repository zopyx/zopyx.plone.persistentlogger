"""Reset the local Plone site and install this add-on.

Executed by ``zconsole run`` with the Zope application bound as ``app``.
This script is intended for local development only.
"""

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
    default_language="de",
    portal_timezone="Europe/Berlin",
)
portal.portal_setup.runAllImportStepsFromProfile(
    "profile-zopyx.plone.persistentlogger:default"
)
commit()
print(f"Created fresh Plone site /{site_id} with zopyx.plone.persistentlogger")
