################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################


import plone.api
import zope.interface
from BTrees.OOBTree import OOBTree
from DateTime import DateTime
from zope.annotation.interfaces import IAnnotations

from .models import LogEvent, Severity
from .repository import AnnotationRepository, _event_date

LOG_KEY = "zopyx.plone.persistentlogger.connector.log"
LOG_LAST_USER = "zopyx.plone.persistentlogger.connector.lastuser"
LOG_LAST_DATE = "zopyx.plone.persistentlogger.connector.lastdate"


class IPersistentLogger(zope.interface.Interface):
    """Marker interface for a object persistent logger"""


@zope.interface.implementer(IPersistentLogger)
class PersistentLoggerAdapter:
    """An adapter for storing logging information as an annotation
    on a persistent object.
    """

    def __init__(self, context):
        self.context = context

    @property
    def entries(self, min_datetime=None, max_datetime=None):
        entries = AnnotationRepository(self.context).events()
        if min_datetime is not None:
            min_datetime = _event_date({"date": min_datetime})
            entries = [entry for entry in entries if _event_date(entry) >= min_datetime]
        if max_datetime is not None:
            max_datetime = _event_date({"date": max_datetime})
            entries = [entry for entry in entries if _event_date(entry) <= max_datetime]
        return entries

    def entry_by_uuid(self, target_uuid):
        """Find a logger entry by UUID."""
        entry = AnnotationRepository(self.context).get(str(target_uuid))
        if entry is not None:
            return entry
        raise ValueError(f"No log entry with UUID {target_uuid} found")

    def __len__(self):
        return len(self.entries)

    @property
    def annotations(self):
        all_annotations = IAnnotations(self.context)
        if LOG_KEY not in all_annotations:
            all_annotations[LOG_KEY] = OOBTree()
        return all_annotations[LOG_KEY]

    def log(self, comment, level="info", username=None, info_url=None, details=None):
        """Add a log entry using the versioned repository schema."""
        current_user = plone.api.user.get_current().getUserName()
        username = username or current_user
        try:
            severity = Severity(level)
        except ValueError:
            # Preserve legacy custom levels while new callers use Severity.
            severity = level
        event = LogEvent(
            comment=comment,
            severity=severity,
            actor=username,
            info_url=info_url,
            details=details,
        )
        AnnotationRepository(self.context).append(event)
        annotations = IAnnotations(self.context)
        annotations[LOG_LAST_USER] = current_user
        annotations[LOG_LAST_DATE] = event.created_at
        self.context.setModificationDate(DateTime())

    def get_last_user(self):
        """Return username of last user"""
        return IAnnotations(self.context).get(LOG_LAST_USER)

    def get_last_date(self):
        """Return datetime of last time used"""
        return IAnnotations(self.context).get(LOG_LAST_DATE)

    def clear(self):
        """Clear all logger entries"""
        annotations = IAnnotations(self.context)
        annotations[LOG_KEY] = OOBTree()
