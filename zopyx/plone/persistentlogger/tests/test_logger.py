# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


import math
import datetime 
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from .base import TestBase
from zopyx.plone.persistentlogger import file_logger
from zopyx.plone.persistentlogger.logger import IPersistentLogger


class BasicTests(TestBase):

    def test_logging(self):
        c = self.portal
        logger = IPersistentLogger(c)
        self.assertEqual(len(logger), 0)
        logger.log(u'error', 'error')
        logger.log(u'info', 'info')
        self.assertEqual(len(logger), 2)
        logger.clear()
        self.assertEqual(len(logger), 0)
        self.assertEqual(logger.get_last_user(), 'test-user')

    def test_entries(self):
        c = self.portal
        logger = IPersistentLogger(c)
        logger.log(u'error', 'error')
        logger.log(u'info', 'info')
        self.assertEqual(len(logger), 2)
        entries = logger.entries
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry, logger.entry_by_uuid(entry['uuid']))
    def test_entry_lookup_and_metadata_failures(self):
        logger = IPersistentLogger(self.portal)
        logger.log(
            u'with details',
            level='warning',
            username='explicit-user',
            details={'key': 'value'},
        )

        entry = next(iter(logger.entries))
        self.assertEqual(entry, logger.entry_by_uuid(entry['uuid']))
        with self.assertRaises(ValueError):
            logger.entry_by_uuid('does-not-exist')
        self.assertIsNotNone(logger.get_last_date())

        logger.log(
            u'text details',
            username='explicit-user',
            details='plain text',
        )


class FileLoggerTests(unittest.TestCase):

    def test_new_logger_creates_isolated_loguru_logger(self):
        logger = file_logger.new_logger()
        self.assertIsNotNone(logger)
        self.assertNotEqual(logger, file_logger.new_logger())

    def test_get_logger_configures_stdout_text_and_json_sinks(self):
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(file_logger, 'new_logger', return_value=logger):
                result = file_logger.get_logger(
                    prefix='application',
                    log_stdout=True,
                    log_as_json=True,
                    log_root=Path(tmpdir),
                )

        self.assertIs(result, logger)
        self.assertEqual(logger.add.call_count, 3)
        logger.info.assert_called_with(
            'Logfile added: {}'.format(Path(tmpdir) / 'application.json')
        )

    def test_get_logger_without_sinks(self):
        logger = MagicMock()
        with patch.object(file_logger, 'new_logger', return_value=logger):
            result = file_logger.get_logger(
                log_stdout=False,
                prefix=None,
                log_as_json=False,
            )

        self.assertIs(result, logger)
        logger.add.assert_not_called()
        logger.info.assert_not_called()


def test_suite():
    from unittest import TestSuite, makeSuite
    suite = TestSuite()
    suite.addTest(makeSuite(BasicTests))
    suite.addTest(makeSuite(FileLoggerTests))
    return suite
