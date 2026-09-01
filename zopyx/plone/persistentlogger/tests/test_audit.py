"""Unit tests for the audit logging subscribers and settings helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from persistent import Persistent

from zopyx.plone.persistentlogger.audit import (
    SNAPSHOT_KEY,
    _diff,
    _jsonable,
    _metadata,
    audit_object_created,
    audit_object_modified,
    audit_settings,
    is_audited,
)


class Context(Persistent):
    __name__ = "context"
    portal_type = "Document"

    def Title(self):
        return "Old title"

    def Description(self):
        return "desc"

    def Subject(self):
        return ("a", "b")

    def Language(self):
        return "de"

    def getId(self):
        return "context"

    def UID(self):
        return "uid-123"


class _AnnotationStore(dict):
    """dict with a persistent-style _p_changed flag."""

    _p_changed = False


class Settings:
    enabled = False
    content_types = []


class AuditUnitTests(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        self.annotation_store = _AnnotationStore()
        self.annotation_patch = patch(
            "zopyx.plone.persistentlogger.audit.IAnnotations",
            return_value=self.annotation_store,
        )
        self.annotation_patch.start()

    def tearDown(self):
        self.annotation_patch.stop()

    def test_settings_fallback_and_is_audited(self):
        with patch(
            "zopyx.plone.persistentlogger.audit.getUtility",
            side_effect=Exception("no registry"),
        ):
            self.assertFalse(audit_settings().enabled)
            self.assertFalse(is_audited(self.context))

        settings = Settings()
        settings.enabled = True
        registry = MagicMock(forInterface=MagicMock(return_value=settings))
        with patch(
            "zopyx.plone.persistentlogger.audit.getUtility",
            return_value=registry,
        ):
            self.assertTrue(is_audited(self.context))

            settings.content_types = ["Event"]
            self.assertFalse(is_audited(self.context))
            self.context.portal_type = "Event"
            self.assertTrue(is_audited(self.context))
            settings.content_types = None
            self.assertTrue(is_audited(self.context))

    def test_metadata_and_diff_helpers(self):
        self.assertEqual(_jsonable(None), None)
        self.assertEqual(_jsonable(1), 1)
        self.assertEqual(_jsonable("x"), "x")
        self.assertEqual(_jsonable(("a", "b")), ["a", "b"])
        self.assertEqual(_jsonable({"k": ("a",)}), {"k": ["a"]})

        meta = _metadata(self.context)
        self.assertEqual(meta["title"], "Old title")
        self.assertEqual(meta["subject"], ["a", "b"])
        self.assertEqual(meta["uid"], "uid-123")
        self.assertEqual(meta["portal_type"], "Document")

        old = {"title": "Old title", "subject": ["a", "b"]}
        new = {"title": "New title", "subject": ["a", "b"]}
        changes = _diff(old, new)
        self.assertEqual(changes["title"]["old"], "Old title")
        self.assertEqual(changes["title"]["new"], "New title")
        self.assertNotIn("subject", changes)
        self.assertEqual(_diff(old, old), {})

    def test_created_logs_entry_and_snapshot(self):
        settings = Settings()
        settings.enabled = True
        with (
            patch(
                "zopyx.plone.persistentlogger.audit.getUtility",
                return_value=MagicMock(forInterface=MagicMock(return_value=settings)),
            ),
            patch(
                "zopyx.plone.persistentlogger.audit.plone.api.user.get_current",
                return_value=MagicMock(getUserName=MagicMock(return_value="manager")),
            ),
            patch("zopyx.plone.persistentlogger.audit.log_event") as log_event_mock,
        ):
            audit_object_created(self.context, MagicMock())
        self.assertEqual(
            self.annotation_store[SNAPSHOT_KEY]["title"], "Old title"
        )
        log_event_mock.assert_called_once()
        kwargs = log_event_mock.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "create")
        self.assertEqual(kwargs["actor"], "manager")
        self.assertEqual(kwargs["details"]["title"], "Old title")

    def test_created_skipped_when_disabled(self):
        with patch(
            "zopyx.plone.persistentlogger.audit.getUtility",
            return_value=MagicMock(forInterface=MagicMock(return_value=Settings())),
        ):
            audit_object_created(self.context, MagicMock())
        self.assertNotIn(SNAPSHOT_KEY, self.annotation_store)

    def test_modified_logs_diff(self):
        settings = Settings()
        settings.enabled = True
        self.annotation_store[SNAPSHOT_KEY] = {
            "title": "Old title",
            "description": "desc",
        }

        class ModifiedContext(Context):
            def Title(self):
                return "New title"

        context = ModifiedContext()
        with (
            patch(
                "zopyx.plone.persistentlogger.audit.getUtility",
                return_value=MagicMock(forInterface=MagicMock(return_value=settings)),
            ),
            patch(
                "zopyx.plone.persistentlogger.audit.plone.api.user.get_current",
                return_value=MagicMock(getUserName=MagicMock(return_value="manager")),
            ),
            patch("zopyx.plone.persistentlogger.audit.log_event") as log_event_mock,
        ):
            audit_object_modified(context, MagicMock())
        self.assertEqual(
            self.annotation_store[SNAPSHOT_KEY]["title"], "New title"
        )
        kwargs = log_event_mock.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "edit")
        self.assertEqual(
            kwargs["details"]["changes"]["title"]["new"], "New title"
        )

    def test_modified_without_changes_is_skipped(self):
        settings = Settings()
        settings.enabled = True
        self.annotation_store[SNAPSHOT_KEY] = _metadata(self.context)
        with (
            patch(
                "zopyx.plone.persistentlogger.audit.getUtility",
                return_value=MagicMock(forInterface=MagicMock(return_value=settings)),
            ),
            patch("zopyx.plone.persistentlogger.audit.log_event") as log_event_mock,
        ):
            audit_object_modified(self.context, MagicMock())
        log_event_mock.assert_not_called()

    def test_modified_without_snapshot_sets_baseline(self):
        settings = Settings()
        settings.enabled = True
        with (
            patch(
                "zopyx.plone.persistentlogger.audit.getUtility",
                return_value=MagicMock(forInterface=MagicMock(return_value=settings)),
            ),
            patch("zopyx.plone.persistentlogger.audit.log_event") as log_event_mock,
        ):
            audit_object_modified(self.context, MagicMock())
        self.assertEqual(
            self.annotation_store[SNAPSHOT_KEY]["title"], "Old title"
        )
        log_event_mock.assert_not_called()

    def test_actor_fallback_on_missing_user(self):
        settings = Settings()
        settings.enabled = True
        with (
            patch(
                "zopyx.plone.persistentlogger.audit.getUtility",
                return_value=MagicMock(forInterface=MagicMock(return_value=settings)),
            ),
            patch(
                "zopyx.plone.persistentlogger.audit.plone.api.user.get_current",
                side_effect=Exception("no user"),
            ),
            patch("zopyx.plone.persistentlogger.audit.log_event") as log_event_mock,
        ):
            audit_object_created(self.context, MagicMock())
        self.assertEqual(log_event_mock.call_args.kwargs["actor"], "")


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(AuditUnitTests)
