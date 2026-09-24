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


class IStorageSettings(Interface):
    """Storage backend selection for the persistent audit log."""

    backend = schema.Choice(
        title="Storage backend",
        description="Where audit records are persisted. The ZODB backend keeps "
        "records in the annotations of the logged object (versioned with the "
        "object, removed with it). The RDBMS backend keeps them in an external "
        "relational database, independent of the content storage.",
        values=["zodb", "rdbms"],
        default="zodb",
    )

    database_url = schema.TextLine(
        title="Database URL",
        description="SQLAlchemy connection URL of the audit database, for "
        "example postgresql+psycopg://user:password@host:5432/database. "
        "Required when the RDBMS backend is selected; when left empty the "
        "environment variable ZOPYX_PERSISTENTLOGGER_DATABASE_URL is used. "
        "Ignored by the ZODB backend.",
        required=False,
        default="",
    )
