"""Control panel view for the site-wide audit logging settings."""

from plone.app.registry.browser.controlpanel import (
    ControlPanelFormWrapper,
    RegistryEditForm,
)
from plone.z3cform import layout
from zope.i18nmessageid import MessageFactory

from zopyx.plone.persistentlogger.interfaces import IAuditLoggingSettings

_ = MessageFactory("zopyx.plone.persistentlogger")


class AuditLoggingSettingsForm(RegistryEditForm):
    schema = IAuditLoggingSettings
    label = _("Audit logging settings")
    description = _("Enable audit logging and choose which content types are recorded.")


AuditLoggingSettingsView = layout.wrap_form(
    AuditLoggingSettingsForm, ControlPanelFormWrapper
)
