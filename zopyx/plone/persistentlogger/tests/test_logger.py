# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


from .base import TestBase


class BasicTests(TestBase):

    def testLogger(self):

        from zopyx.plone.persistentlogger.logger import IPersistentLogger
        c = self.portal
        logger = IPersistentLogger(c)
        self.assertEqual(len(logger), 0)
        logger.log(u'error', 'error')
        logger.log(u'info', 'info')
        self.assertEqual(len(logger), 2)
        logger.clear()
        self.assertEqual(len(logger), 0)


def test_suite():
    from unittest2 import TestSuite, makeSuite
    suite = TestSuite()
    suite.addTest(makeSuite(BasicTests))
    return suite
