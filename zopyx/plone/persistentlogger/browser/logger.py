# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################

import json
import operator
import datetime

from zope.interface import alsoProvides

from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from plone.protect.interfaces import IDisableCSRFProtection

from zopyx.plone.persistentlogger.logger import IPersistentLogger


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, set):
        return list(obj)
    raise TypeError('Type not serializable ({})'.format(obj))


class Logging(BrowserView):

    template = ViewPageTemplateFile('logger.pt')

    def demo(self):
        """ Create demo logger entries """
        import time
        import random
        alsoProvides(self.request, IDisableCSRFProtection)
        logger = IPersistentLogger(self.context)
        for i in range(20):
            text = u'some text üöä {}'.format(i)
            level = random.choice(['debug', 'info', 'warn', 'error', 'fatal'])
            details = dict(a=random.random(), b=random.random(), c=random.random())
            logger.log(comment=text, level=level, details=details)
            time.sleep(0.3)
        self.context.plone_utils.addPortalMessage(u'Demo logger entries created')
        self.request.response.redirect(self.context.absolute_url() + '/@@persistent-log')

    def entries(self):
        alsoProvides(self.request, IDisableCSRFProtection)
        result = list(IPersistentLogger(self.context).entries)
        result = sorted(result, key=operator.itemgetter('date'))
        return result

    def entries_json(self, date_fmt='%d.%m.%Y %H:%M:%S'):
        result = list()
        for d in self.entries():
            d = d.copy()
            d['date_str'] = d['date'].strftime(date_fmt)
            result.append(d)
        return json.dumps(result[::-1], default=json_serial)

    def log_clear(self):
        """ Clear persistent log """
        alsoProvides(self.request, IDisableCSRFProtection)
        logger = IPersistentLogger(self.context)
        logger.clear()
        msg = u'Log entries cleared'
        logger.log(msg, 'info')
        self.context.plone_utils.addPortalMessage(msg)
        return self.request.response.redirect(
            '{}/persistent-log'.format(self.context.absolute_url()))

    def __call__(self):
        return self.template()
