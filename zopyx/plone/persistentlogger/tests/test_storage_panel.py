"""Integration tests for the *Audit log storage* control panel."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from plone.registry.interfaces import IRegistry
from zope.component import getUtility

from ..interfaces import IStorageSettings
from ..storage import AnnotationRepository, get_repository
from ..storage import factory as storage_factory
from ..storage.factory import BACKEND_RDBMS, BACKEND_ZODB
from ..tests.base import POLICY_INTEGRATION_TESTING

POSTGRES_URL = "postgresql+psycopg://logger:secret@localhost:5432/audit"


class StoragePanelTests(unittest.TestCase):
    layer = POLICY_INTEGRATION_TESTING

    def setUp(self):
        self.portal = self.layer["portal"]
        from AccessControl.SecurityManagement import newSecurityManager

        user = self.portal.acl_users.getUser("god")
        newSecurityManager(None, user.__of__(self.portal.acl_users))
        storage_factory.clear_cache()
        self.addCleanup(storage_factory.clear_cache)
        self.settings = getUtility(IRegistry).forInterface(
            IStorageSettings, check=False
        )
        self.settings.backend = BACKEND_ZODB
        self.settings.database_url = ""

    # ------------------------------------------------------------------
    # registration and rendering
    # ------------------------------------------------------------------
    def test_registry_records_are_installed_with_zodb_default(self):
        self.assertEqual(self.settings.backend, BACKEND_ZODB)
        self.assertEqual(self.settings.database_url, "")

    def test_configlet_is_registered(self):
        configlet = self.portal.restrictedTraverse("portal_controlpanel")
        actions = {action.getId(): action for action in configlet.listActions()}
        action = actions["zopyx.plone.persistentlogger.storage"]
        self.assertEqual(action.title, "Audit log storage")
        self.assertIn("@@audit-storage-settings", action.getActionExpression())
        self.assertEqual(action.permissions, ("Manage portal",))

    def test_control_panel_renders_both_fields(self):
        view = self.portal.restrictedTraverse("@@audit-storage-settings")
        html = view()
        self.assertIn("Audit log storage", html)
        self.assertIn("Storage backend", html)
        self.assertIn("Database URL", html)
        self.assertIn('name="form.widgets.backend:list"', html)
        self.assertIn('name="form.widgets.database_url"', html)
        self.assertIn('value="zodb"', html)
        self.assertIn('value="rdbms"', html)
        # Both inherited and re-declared buttons are rendered.
        self.assertIn('name="form.buttons.save"', html)
        self.assertIn('name="form.buttons.cancel"', html)

    # ------------------------------------------------------------------
    # saving
    # ------------------------------------------------------------------
    def submit(self, **form_data):
        """Submit the control panel form the way a browser would."""
        from ..browser.storage import StorageSettingsForm

        request = self.portal.REQUEST
        request.method = "POST"
        request.form.clear()
        request.form.update(form_data)
        request.form["form.buttons.save"] = "Save"
        form = StorageSettingsForm(self.portal, request)
        form.update()
        status = form.status or ""
        request.response.setStatus(200)
        request.form.clear()
        storage_factory.clear_cache()
        return status

    def save(self, backend, database_url=""):
        # The select widget renders ``form.widgets.backend:list``; the Zope
        # publisher strips the ``:list`` marker and hands the value to the
        # widget under the bare name.
        return self.submit(
            **{
                "form.widgets.backend": [backend],
                "form.widgets.database_url": database_url,
            }
        )

    def stored(self):
        return getUtility(IRegistry).forInterface(IStorageSettings, check=False)

    def test_saving_the_zodb_backend_stores_the_setting(self):
        # Start from a non-default configuration so that the assertions
        # below prove the form actually wrote the registry.
        self.settings.backend = BACKEND_RDBMS
        self.settings.database_url = POSTGRES_URL
        self.assertEqual(self.save(BACKEND_ZODB), "")
        self.assertEqual(self.stored().backend, BACKEND_ZODB)
        self.assertIsInstance(get_repository(self.portal), AnnotationRepository)

    def test_invalid_widget_input_reports_form_errors(self):
        status = self.save("postgres")
        self.assertEqual(status, "There were some errors.")
        self.assertEqual(self.stored().backend, BACKEND_ZODB)

    def test_rdbms_without_a_url_is_refused(self):
        status = self.save(BACKEND_RDBMS)
        self.assertIn("database URL is required", status)
        self.assertEqual(self.stored().backend, BACKEND_ZODB)

    def test_rdbms_with_an_invalid_url_is_refused(self):
        status = self.save(BACKEND_RDBMS, "not a url")
        self.assertIn("not valid", status)
        self.assertEqual(self.stored().backend, BACKEND_ZODB)

    def test_rdbms_with_unreachable_database_is_refused(self):
        with patch(
            "zopyx.plone.persistentlogger.browser.storage.check_database_connection",
            return_value="Could not connect to the configured database: refused",
        ) as check:
            status = self.save(BACKEND_RDBMS, POSTGRES_URL)
        check.assert_called_once_with(POSTGRES_URL)
        self.assertIn("Could not connect", status)
        self.assertEqual(self.stored().backend, BACKEND_ZODB)
        self.assertEqual(self.stored().database_url, "")

    def test_rdbms_with_reachable_database_is_stored(self):
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.storage.check_database_connection",
                return_value=None,
            ),
            patch(
                "zopyx.plone.persistentlogger.storage.factory.build_rdbms_repository",
                return_value="rdbms-repository",
            ),
        ):
            status = self.save(BACKEND_RDBMS, POSTGRES_URL)
            repository = get_repository(self.portal)
        self.assertEqual(status, "")
        self.assertEqual(self.stored().backend, BACKEND_RDBMS)
        self.assertEqual(self.stored().database_url, POSTGRES_URL)
        self.assertEqual(repository, "rdbms-repository")

    def test_cancel_redirects_to_the_control_panel_overview(self):
        from ..browser.storage import StorageSettingsForm

        request = self.portal.REQUEST
        request.form.clear()
        form = StorageSettingsForm(self.portal, request)
        form.update()
        StorageSettingsForm.handleCancel(form, MagicMock())
        self.assertEqual(request.response.getStatus(), 302)
        self.assertIn("overview-controlpanel", request.response.getHeader("location"))
        request.response.setStatus(200)

    def test_settings_change_is_not_cached(self):
        self.settings.backend = BACKEND_RDBMS
        self.settings.database_url = POSTGRES_URL
        with patch(
            "zopyx.plone.persistentlogger.storage.factory.build_rdbms_repository",
            return_value="rdbms-repository",
        ):
            self.assertEqual(get_repository(self.portal), "rdbms-repository")
        self.settings.backend = BACKEND_ZODB
        self.assertIsInstance(get_repository(self.portal), AnnotationRepository)


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(StoragePanelTests)
