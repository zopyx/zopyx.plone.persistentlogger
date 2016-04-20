# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


from zope.interface import alsoProvides

from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from plone.protect.interfaces import IDisableCSRFProtection

from zopyx.plone.persistentlogger.logger import IPersistentLogger


class Logging(BrowserView):

    template = ViewPageTemplateFile('logger.pt')

    def entries(self):
        alsoProvides(self.request, IDisableCSRFProtection)
        return IPersistentLogger(self.context).entries

    def log_clear(self):
        """ Clear persistent log """
        alsoProvides(self.request, IDisableCSRFProtection)
        IPersistentLogger(self.context).clear()
        msg = u'Log entries cleared'
        self.context.plone_utils.addPortalMessage(msg)
        return self.request.response.redirect(
            '{}/persistent-log'.format(self.context.absolute_url()))

    def __call__(self):
        return self.template()
