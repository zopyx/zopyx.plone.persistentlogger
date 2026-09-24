"""Tests for the agGrid entries view and its server side data source.

The data source is what makes the grid work server side: the browser sends
agGrid's ``startRow``/``endRow``/``sortModel``/``filterModel`` plus the quick
filter, and receives one page of rows plus the total number of matches.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from persistent import Persistent

from ..browser import logger as logger_view
from ..browser.logger import Logging
from ..models import LogEvent, Severity
from ..storage import COLUMNS, AnnotationRepository
from ..storage import factory as storage_factory


class Context(Persistent):
    __name__ = "context"

    def absolute_url(self):
        return "https://example.test/context"


class Response:
    """Minimal stand in for the Zope response object."""

    def __init__(self):
        self.headers = {}
        self.status = None

    def setHeader(self, name, value):
        self.headers[name] = value

    def setStatus(self, status, reason=None):
        self.status = status


class Request:
    """Minimal stand in for the Zope request object."""

    def __init__(self, params=None, method="GET"):
        self.form = dict(params or {})
        self.method = method
        self.response = Response()

    def get(self, name, default=None):
        return self.form.get(name, default)


class EntriesViewTests(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        self.annotations = {}
        self.patch_annotations = patch(
            "zopyx.plone.persistentlogger.storage.zodb.IAnnotations",
            return_value=self.annotations,
        )
        self.patch_annotations.start()
        self.addCleanup(storage_factory.clear_cache)
        self.repository = AnnotationRepository(self.context)
        self.patch_repository = patch(
            "zopyx.plone.persistentlogger.browser.logger.get_repository",
            return_value=self.repository,
        )
        self.patch_repository.start()
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def tearDown(self):
        self.patch_repository.stop()
        self.patch_annotations.stop()

    def append(self, index, **kwargs):
        options = {
            "severity": Severity.INFO,
            "actor": "manager",
            "event_type": "governance",
            "target": "context",
            "details": {"index": index},
        }
        options.update(kwargs)
        return self.repository.append(
            LogEvent(
                comment=f"entry {index}",
                created_at=self.now + timedelta(minutes=index),
                **options,
            )
        )

    def call(self, params=None):
        request = Request(params)
        payload = json.loads(Logging(self.context, request).entries_data())
        return payload, request

    def test_newest_first_page_with_total(self):
        for index in range(5):
            self.append(index)
        payload, request = self.call({"startRow": "0", "endRow": "2"})
        self.assertEqual(
            [row["comment"] for row in payload["rows"]], ["entry 4", "entry 3"]
        )
        self.assertEqual(payload["lastRow"], 5)
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["startRow"], 0)
        self.assertEqual(payload["endRow"], 2)
        self.assertEqual(request.response.headers["Content-Type"], "application/json")

    def test_second_page_and_unbounded_request(self):
        for index in range(5):
            self.append(index)
        page, _ = self.call({"startRow": "2", "endRow": "4"})
        self.assertEqual(
            [row["comment"] for row in page["rows"]], ["entry 2", "entry 1"]
        )
        self.assertEqual(page["startRow"], 2)
        everything, _ = self.call({"startRow": "0", "endRow": "-1"})
        self.assertEqual(everything["rows"], [])
        self.assertEqual(everything["total"], 5)
        without_end_row, _ = self.call({"startRow": "0"})
        self.assertEqual(without_end_row["rows"], [])
        self.assertEqual(without_end_row["total"], 5)

    def test_page_size_is_capped(self):
        for index in range(3):
            self.append(index)
        with patch.object(logger_view, "MAX_PAGE_SIZE", 2):
            payload, _ = self.call({"startRow": "0", "endRow": "1000"})
        self.assertEqual(len(payload["rows"]), 2)

    def test_sort_model_is_applied(self):
        for index in range(3):
            self.append(index)
        payload, _ = self.call(
            {
                "startRow": "0",
                "endRow": "10",
                "sortModel": json.dumps([{"colId": "comment", "sort": "asc"}]),
            }
        )
        self.assertEqual(
            [row["comment"] for row in payload["rows"]],
            ["entry 0", "entry 1", "entry 2"],
        )

    def test_filter_model_is_applied(self):
        self.append(0)
        self.append(1, severity=Severity.ERROR)
        payload, _ = self.call(
            {
                "startRow": "0",
                "endRow": "10",
                "filterModel": json.dumps(
                    {
                        "comment": {
                            "filterType": "text",
                            "type": "contains",
                            "filter": "entry 1",
                        }
                    }
                ),
            }
        )
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["severity"], "error")

    def test_quick_filter_and_row_shape(self):
        entry = self.append(0)
        payload, _ = self.call({"startRow": "0", "endRow": "10", "quick": "manager"})
        self.assertEqual(payload["total"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["uuid"], entry["uuid"])
        self.assertEqual(row["actor"], "manager")
        self.assertEqual(row["created_at"], self.now.isoformat())
        self.assertEqual(row["details"], {"index": 0})
        self.assertIsNone(row["info_url"])
        self.assertEqual(row["schema_version"], 1)
        self.assertIsInstance(row["created_at"], str)
        self.assertEqual(self.call({"quick": "nothing"})[0]["total"], 0)

    def test_invalid_payloads_are_reported_as_bad_request(self):
        payload, request = self.call(
            {"filterModel": json.dumps({"nope": {"filterType": "text"}})}
        )
        self.assertEqual(request.response.status, 400)
        self.assertIn("unknown filter column", payload["error"])

        payload, request = self.call({"startRow": "lots"})
        self.assertEqual(request.response.status, 400)
        self.assertIn("startRow", payload["error"])

        payload, request = self.call({"startRow": ["0", "1"]})
        self.assertEqual(request.response.status, 400)
        self.assertIn("invalid startRow parameter", payload["error"])

        payload, request = self.call({"sortModel": json.dumps([{"colId": "nope"}])})
        self.assertEqual(request.response.status, 400)
        self.assertIn("unknown sort column", payload["error"])

    def test_count_reports_the_total_number_of_entries(self):
        self.assertEqual(Logging(self.context, Request()).count(), 0)
        for index in range(4):
            self.append(index)
        self.assertEqual(Logging(self.context, Request()).count(), 4)

    def test_grid_configuration_is_shared_with_the_parser(self):
        view = Logging(self.context, Request())
        config = json.loads(view.grid_config())
        self.assertEqual(
            config["dataUrl"], "https://example.test/context/@@persistent-log-data"
        )
        self.assertEqual(
            [column["field"] for column in config["columns"]],
            [column.field for column in COLUMNS],
        )
        self.assertGreater(config["pageSize"], 0)
        self.assertEqual(json.loads(view.grid_columns()), config["columns"])


def test_suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(EntriesViewTests)
