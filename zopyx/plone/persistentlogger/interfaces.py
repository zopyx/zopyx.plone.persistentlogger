################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


from zope import schema
from zope.interface import Interface


class BrowserLayer(Interface):
    pass


class DemoBrowserLayer(BrowserLayer):
    pass


class IAuditLoggingSettings(Interface):
    """Site-wide audit logging configuration (registry records)."""

    enabled = schema.Bool(
        title="Audit logging enabled",
        description="Log content creation and metadata changes on this site.",
        default=False,
    )

    content_types = schema.List(
        title="Audited content types",
        description="Restrict audit logging to these content types. "
        "Leave empty to audit all types.",
        value_type=schema.Choice(
            vocabulary="plone.app.vocabularies.ReallyUserFriendlyTypes"
        ),
        default=[],
        required=False,
    )
