# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################

import json
import datetime
import pkg_resources

from zope.interface import alsoProvides

from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from plone.protect.interfaces import IDisableCSRFProtection

from zopyx.plone.persistentlogger.logger import IPersistentLogger


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, datetime.datetime):
        serial = obj.isoformat()
        return serial
    raise TypeError ("Type not serializable")


class Logging(BrowserView):

    template = ViewPageTemplateFile('logger.pt')

    def is_plone5(self):
        return pkg_resources.get_distribution('Products.CMFPlone').version.startswith('5')

    def entries(self):
        alsoProvides(self.request, IDisableCSRFProtection)
        return IPersistentLogger(self.context).entries

    def entries_json(self, date_fmt='%d.%m.%Y %H:%M:%S'):
        result = list()
        for d in self.entries():
            d = d.copy()
            d['date_str'] = d['date'].strftime(date_fmt)
            result.append(d)
        return json.dumps(result, default=json_serial)

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
