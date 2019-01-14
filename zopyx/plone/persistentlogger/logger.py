# -*- coding: utf-8 -*-

################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################

import uuid
import logging
import datetime
import pprint

import plone.api
import zope.interface
from DateTime import DateTime
from persistent.list import PersistentList
from zope.annotation.interfaces import IAnnotations
from BTrees.OOBTree import OOBTree


LOG_KEY =       'zopyx.plone.persistentlogger.connector.log'
LOG_LAST_USER = 'zopyx.plone.persistentlogger.connector.lastuser'
LOG_LAST_DATE = 'zopyx.plone.persistentlogger.connector.lastdate'


class IPersistentLogger(zope.interface.Interface):
    """ Marker interface for a object persistent logger """


@zope.interface.implementer(IPersistentLogger)
class PersistentLoggerAdapter(object):
    """ An adapter for storing logging information as an annotation
        on a persistent object.
    """

    def __init__(self, context):
        self.context = context

    @property
    def entries(self, min_datetime=None, max_datetime=None):
        return self.annotations.values(min_datetime, max_datetime)

    def entry_by_uuid(self, target_uuid):
        """ Find a logger entry by uuid """
        for entry in self.entries:
            if target_uuid == entry.get('uuid'):
                return entry
        raise ValueError(
            u'No log entry with UUID {} found'.format(target_uuid))

    def __len__(self):
        return len(self.entries)

    @property
    def annotations(self):
        all_annotations = IAnnotations(self.context)
        if LOG_KEY not in all_annotations:
            all_annotations[LOG_KEY] = OOBTree()
        return all_annotations[LOG_KEY]

    def log(self, comment, level='info', username=None, info_url=None, details=None):
        """ Add a log entry """
        annotations = self.annotations
        details_raw = None
        if details:
            if not isinstance(details, str):
                details_raw = details
                details = pprint.pformat(details)
        if not username:
            username=plone.api.user.get_current().getUserName()
        d = dict(date=datetime.datetime.utcnow(),
                 username=username,
                 level=level,
                 info_url=info_url,
                 details=details,
                 details_raw=details_raw,
                 uuid=str(uuid.uuid1()),
                 comment=comment)
        annotations[d['date']] = d
        annotations._p_changed = 1
        IAnnotations(self.context)[LOG_LAST_USER] = plone.api.user.get_current().getUserName()
        IAnnotations(self.context)[LOG_LAST_DATE] = datetime.datetime.utcnow()
        self.context.setModificationDate(DateTime())

    def get_last_user(self):
        """ Return username of last user """
        return IAnnotations(self.context).get(LOG_LAST_USER)

    def get_last_date(self):
        """ Return datetime of last time used """
        return IAnnotations(self.context).get(LOG_LAST_DATE)

    def clear(self):
        """ Clear all logger entries """
        annotations = IAnnotations(self.context)
        annotations[LOG_KEY] = OOBTree()
