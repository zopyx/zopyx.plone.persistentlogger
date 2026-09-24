################################################################
# zopyx.plone.persistentlogger
# (C) 2015,  Andreas Jung, www.zopyx.com, Tuebingen, Germany
################################################################

import datetime
import json
import operator

from plone.protect import CheckAuthenticator
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from zopyx.plone.persistentlogger.logger import IPersistentLogger
from zopyx.plone.persistentlogger.storage import (
    QueryError,
    columns_json,
    default_sort,
    event_date,
    get_repository,
    parse_filter_model,
    parse_sort_model,
    severity_value,
)

#: Hard upper bound for one page handed to the grid.
MAX_PAGE_SIZE = 500


def _param(request, name, default=None):
    """Return a request parameter from the query string or the form."""
    value = request.form.get(name, default)
    if value is None:
        value = request.get(name, default)
    return value


def _int_param(request, name, default):
    """Return an integer request parameter or raise :class:`QueryError`."""
    value = _param(request, name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise QueryError(f"invalid integer parameter {name!r}") from exc


def grid_row(entry):
    """Map one record onto the row shape the grid columns expect."""
    return {
        "uuid": str(entry.get("uuid", "")),
        "created_at": event_date(entry).isoformat(),
        "severity": severity_value(entry),
        "actor": str(entry.get("username", entry.get("actor", "")) or ""),
        "event_type": str(entry.get("event_type", "") or ""),
        "target": str(entry.get("target", "") or ""),
        "comment": str(entry.get("comment", "") or ""),
        "info_url": entry.get("info_url"),
        "details": entry.get("details_raw", entry.get("details")),
        "schema_version": int(entry.get("schema_version", 1) or 1),
    }


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Type not serializable ({obj})")


class Logging(BrowserView):
    template = ViewPageTemplateFile("logger.pt")

    def demo(self):
        """Create demo logger entries"""
        import random
        import time

        CheckAuthenticator(self.request)
        logger = IPersistentLogger(self.context)
        for i in range(20):
            text = f"some text üöä {i}"
            level = random.choice(["debug", "info", "warn", "error", "fatal"])
            details = dict(a=random.random(), b=random.random(), c=random.random())
            logger.log(comment=text, level=level, details=details)
            time.sleep(0.3)
        self.context.plone_utils.addPortalMessage("Demo logger entries created")
        self.request.response.redirect(
            self.context.absolute_url() + "/@@persistent-log"
        )

    def entries(self):
        result = list(IPersistentLogger(self.context).entries)
        result = sorted(result, key=operator.itemgetter("date"))
        return result

    def grid_columns(self):
        """Return the agGrid column definitions (shared with the parser)."""
        return columns_json()

    def grid_config(self):
        """Return the configuration the grid script reads from the page."""
        return json.dumps(
            {
                "dataUrl": f"{self.context.absolute_url()}/@@persistent-log-data",
                "columns": json.loads(columns_json()),
                "pageSize": 25,
                "cacheBlockSize": 100,
            }
        )

    def entries_data(self):
        """Return one page of entries for the agGrid data source.

        The grid uses the infinite row model, so paging, sorting and filtering
        all happen here: the request carries ``startRow``/``endRow``,
        ``sortModel`` and ``filterModel`` (agGrid payloads) plus the optional
        quick filter, and the response carries one page plus the total number
        of matching records.
        """
        request = self.request
        try:
            conditions = parse_filter_model(_param(request, "filterModel"))
            sort = parse_sort_model(_param(request, "sortModel")) or default_sort()
            offset = max(_int_param(request, "startRow", 0), 0)
            end_row = _int_param(request, "endRow", -1)
            quick = str(_param(request, "quick") or "")
        except QueryError as exc:
            request.response.setStatus(400)
            request.response.setHeader("Content-Type", "application/json")
            return json.dumps({"error": str(exc)})

        limit = None if end_row < 0 else min(max(end_row - offset, 0), MAX_PAGE_SIZE)
        result = get_repository(self.context).search(
            conditions=conditions,
            sort=sort,
            offset=offset,
            limit=limit,
            quick=quick,
        )
        rows = [grid_row(entry) for entry in result.rows]
        request.response.setHeader("Content-Type", "application/json")
        return json.dumps(
            {
                "rows": rows,
                "startRow": offset,
                "endRow": offset + len(rows),
                "lastRow": result.total,
                "total": result.total,
            },
            default=json_serial,
        )

    def entries_json(self, date_fmt="%d.%m.%Y %H:%M:%S"):
        result = list()
        for d in self.entries():
            d = d.copy()
            d["date_str"] = d["date"].strftime(date_fmt)
            result.append(d)
        return json.dumps(result[::-1], default=json_serial)

    def count(self):
        """Return the number of stored entries (without loading them)."""
        return get_repository(self.context).search(limit=0).total

    def last_user(self):
        return IPersistentLogger(self.context).get_last_user()

    def last_date(self):
        return IPersistentLogger(self.context).get_last_date()

    def is_manager(self):
        return self.context.portal_membership.checkPermission(
            "cmf.ManagePortal", self.context
        )

    def level_class(self, level):
        return {
            "debug": "text-bg-secondary",
            "info": "text-bg-info",
            "warning": "text-bg-warning",
            "warn": "text-bg-warning",
            "error": "text-bg-danger",
            "fatal": "text-bg-danger",
            "critical": "text-bg-danger",
        }.get(str(level), "text-bg-light")

    def __call__(self):
        return self.template()
