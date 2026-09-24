"""Control panel view for the audit log storage backend."""

from __future__ import annotations

import plone.api
from plone.app.registry.browser.controlpanel import (
    ControlPanelFormWrapper,
    RegistryEditForm,
)
from plone.z3cform import layout
from z3c.form import button
from zope.component.hooks import getSite
from zope.i18nmessageid import MessageFactory

from ..interfaces import IStorageSettings
from ..storage.factory import (
    BACKEND_RDBMS,
    check_database_connection,
    validate_storage_configuration,
)

_ = MessageFactory("zopyx.plone.persistentlogger")


class StorageSettingsForm(RegistryEditForm):
    """Edit the storage backend and the database URL.

    z3c.form registers button handlers per class, so the inherited "Save"
    handler cannot simply be wrapped: re-declaring it would drop the
    inherited "Cancel" button. Both handlers are therefore declared here;
    the cancel handler replicates ``RegistryEditForm.handleCancel``.
    """

    schema = IStorageSettings
    label = "Audit log storage"
    description = (
        "Choose where the persistent audit log stores its records. "
        "Switching backends does not migrate existing records."
    )

    @button.buttonAndHandler(_("Save"), name="save")
    def handleSave(self, action):
        data, errors = self.extractData()
        if errors:
            self.status = self.formErrorsMessage
            return
        message = validate_storage_configuration(
            data.get("backend"), data.get("database_url")
        )
        if message is None and data.get("backend") == BACKEND_RDBMS:
            message = check_database_connection(data.get("database_url") or "")
        if message:
            # Keep the status in the form and leave the registry untouched.
            self.status = message
            return
        self.applyChanges(data)
        plone.api.portal.show_message(
            _("Changes saved."), request=self.request, type="info"
        )
        self.request.response.redirect(self.request.getURL())

    @button.buttonAndHandler(_("Cancel"), name="cancel")
    def handleCancel(self, action):
        plone.api.portal.show_message(
            _("Changes canceled."), request=self.request, type="info"
        )
        self.request.response.redirect(
            f"{getSite().absolute_url()}/{self.control_panel_view}"
        )


StorageSettingsView = layout.wrap_form(StorageSettingsForm, ControlPanelFormWrapper)
