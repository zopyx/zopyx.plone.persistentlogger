# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


import math
import datetime 

from .base import TestBase
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



def test_suite():
    from unittest import TestSuite, makeSuite
    suite = TestSuite()
    suite.addTest(makeSuite(BasicTests))
    return suite
