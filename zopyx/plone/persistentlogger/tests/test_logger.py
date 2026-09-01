################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from zopyx.plone.persistentlogger import file_logger
from zopyx.plone.persistentlogger.browser.logger import Logging, json_serial
from zopyx.plone.persistentlogger.logger import IPersistentLogger

from .base import TestBase


class BasicTests(TestBase):
    def test_logging(self):
        c = self.portal
        logger = IPersistentLogger(c)
        self.assertEqual(len(logger), 0)
        logger.log("error", "error")
        logger.log("info", "info")
        self.assertEqual(len(logger), 2)
        logger.clear()
        self.assertEqual(len(logger), 0)
        self.assertEqual(logger.get_last_user(), "test-user")

    def test_entries(self):
        c = self.portal
        logger = IPersistentLogger(c)
        logger.log("error", "error")
        logger.log("info", "info")
        self.assertEqual(len(logger), 2)
        entries = logger.entries
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry, logger.entry_by_uuid(entry["uuid"]))

    def test_entry_lookup_and_metadata_failures(self):
        logger = IPersistentLogger(self.portal)
        logger.log(
            "with details",
            level="warning",
            username="explicit-user",
            details={"key": "value"},
        )

        entry = next(iter(logger.entries))
        self.assertEqual(entry, logger.entry_by_uuid(entry["uuid"]))
        with self.assertRaises(ValueError):
            logger.entry_by_uuid("does-not-exist")
        self.assertIsNotNone(logger.get_last_date())

        logger.log(
            "text details",
            username="explicit-user",
            details="plain text",
        )

    def test_legacy_level_filters_and_annotations(self):
        logger = IPersistentLogger(self.portal)
        logger.annotations
        logger.log("custom", level="custom", username="explicit")
        self.assertEqual(logger.entries[0]["level"], "custom")
        entries = type(logger).entries.fget(logger, min_datetime=datetime.datetime.min)
        self.assertEqual(len(entries), 1)
        entries = type(logger).entries.fget(logger, max_datetime=datetime.datetime.max)
        self.assertEqual(len(entries), 1)

    def test_zz_login_helper(self):
        self.login("god")
        self.assertIsNotNone(self.portal.acl_users.getUser("god"))


class FileLoggerTests(unittest.TestCase):
    def test_new_logger_creates_isolated_loguru_logger(self):
        logger = file_logger.new_logger()
        self.assertIsNotNone(logger)
        self.assertNotEqual(logger, file_logger.new_logger())

    def test_new_logger_falls_back_when_private_api_missing(self):
        original_import = __import__("builtins").__import__

        def blocked_import(name, *args, **kwargs):
            if name == "loguru._logger":
                raise ImportError(name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocked_import):
            logger = file_logger.new_logger()
        self.assertIsNotNone(logger)
        self.assertNotEqual(logger, file_logger.new_logger())

    def test_get_logger_configures_stdout_text_and_json_sinks(self):
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(file_logger, "new_logger", return_value=logger):
                result = file_logger.get_logger(
                    prefix="application",
                    log_stdout=True,
                    log_as_json=True,
                    log_root=Path(tmpdir),
                )

        self.assertIs(result, logger)
        self.assertEqual(logger.add.call_count, 3)
        logger.info.assert_called_with(
            "Logfile added: {}".format(Path(tmpdir) / "application.json")
        )

    def test_get_logger_without_sinks(self):
        logger = MagicMock()
        with patch.object(file_logger, "new_logger", return_value=logger):
            result = file_logger.get_logger(
                log_stdout=False,
                prefix=None,
                log_as_json=False,
            )

        self.assertIs(result, logger)
        logger.add.assert_not_called()
        logger.info.assert_not_called()


class BrowserLoggerTests(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.context.absolute_url.return_value = "https://example.test/item"
        self.request = MagicMock()
        self.view = Logging(self.context, self.request)

    def test_json_serial(self):
        value = datetime.datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(json_serial(value), "2026-01-02T03:04:05")
        self.assertCountEqual(json_serial({"one", "two"}), ["one", "two"])
        with self.assertRaises(TypeError):
            json_serial(object())

    def test_entries_and_entries_json(self):
        entries = [
            {"date": datetime.datetime(2026, 1, 2, 3, 4, 5), "comment": "new"},
            {"date": datetime.datetime(2026, 1, 1, 3, 4, 5), "comment": "old"},
        ]
        adapter = MagicMock()
        adapter.entries = entries
        with patch(
            "zopyx.plone.persistentlogger.browser.logger.IPersistentLogger",
            return_value=adapter,
        ):
            result = self.view.entries()
            payload = self.view.entries_json()

        self.assertEqual(result, [entries[1], entries[0]])
        self.assertIn('"comment": "new"', payload)
        self.assertIn('"date_str": "02.01.2026 03:04:05"', payload)

    def test_demo_and_call(self):
        adapter = MagicMock()
        self.context.plone_utils = MagicMock()
        self.view.template = MagicMock(return_value="rendered")
        with (
            patch(
                "zopyx.plone.persistentlogger.browser.logger.IPersistentLogger",
                return_value=adapter,
            ),
            patch("zopyx.plone.persistentlogger.browser.logger.CheckAuthenticator"),
            patch("time.sleep"),
        ):
            self.view.demo()
            rendered = self.view()

        self.assertEqual(adapter.log.call_count, 20)
        self.assertEqual(rendered, "rendered")
        self.context.plone_utils.addPortalMessage.assert_called_once()


def test_suite():
    from unittest import TestLoader, TestSuite

    loader = TestLoader()
    suite = TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(BasicTests))
    suite.addTest(loader.loadTestsFromTestCase(FileLoggerTests))
    suite.addTest(loader.loadTestsFromTestCase(BrowserLoggerTests))
    return suite
