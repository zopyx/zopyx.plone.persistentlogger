"""Control panel view for the site-wide audit logging settings."""

from plone.app.registry.browser.controlpanel import (
    ControlPanelFormWrapper,
    RegistryEditForm,
)
from plone.z3cform import layout

from zopyx.plone.persistentlogger.interfaces import IAuditLoggingSettings


class AuditLoggingSettingsForm(RegistryEditForm):
    schema = IAuditLoggingSettings
    label = "Audit logging"
    description = "Log content creation and metadata changes on this site."


AuditLoggingSettingsView = layout.wrap_form(
    AuditLoggingSettingsForm, ControlPanelFormWrapper
)
