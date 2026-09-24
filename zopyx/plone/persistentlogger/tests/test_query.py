"""Unit tests for the backend independent query model (agGrid payloads)."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from ..storage import query as query_model
from ..storage.query import (
    COLUMNS,
    TEXT,
    Column,
    Condition,
    ConditionGroup,
    QueryError,
    SortSpec,
    column_of,
    columns_json,
    default_sort,
    matches,
    parse_filter_model,
    parse_sort_model,
    quick_matches,
    sort_entries,
)


def make_entry(index: int = 0, **kwargs):
    """Return a canonical event record like the storage layer returns."""
    created_at = kwargs.pop("created_at", datetime(2026, 1, 1, index, 0, tzinfo=UTC))
    entry = {
        "uuid": str(uuid4()),
        "event_id": None,
        "created_at": created_at,
        "date": created_at,
        "severity": "info",
        "level": "info",
        "actor": "alice",
        "username": "alice",
        "event_type": "application",
        "target": "content",
        "comment": f"entry {index}",
        "info_url": None,
        "details": {"source": "test", "index": index},
        "details_raw": {"source": "test", "index": index},
        "schema_version": 1,
    }
    entry.pop("event_id")
    entry.update(kwargs)
    return entry


def model(**kwargs) -> str:
    """Return a filter model JSON string with one column."""
    return json.dumps(kwargs)


def only_condition(group: ConditionGroup) -> Condition:
    """Return the single condition of a parsed filter model."""
    item = group.conditions[0]
    assert isinstance(item, Condition)  # noqa: S101 - test helper
    return item


def only_group(group: ConditionGroup) -> ConditionGroup:
    """Return the single nested group of a parsed filter model."""
    item = group.conditions[0]
    assert isinstance(item, ConditionGroup)  # noqa: S101 - test helper
    return item


class ColumnsTests(unittest.TestCase):
    def test_columns_are_unique_and_queryable(self):
        fields = [column.field for column in COLUMNS]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertIn("created_at", fields)
        for column in COLUMNS:
            self.assertEqual(column_of(column.field), column)
        self.assertIsNone(column_of("nope"))

    def test_columns_json_describes_the_columns(self):
        payload = json.loads(columns_json())
        self.assertEqual(
            [item["field"] for item in payload], [c.field for c in COLUMNS]
        )
        self.assertEqual(payload[1]["headerName"], "Level")
        self.assertEqual(payload[0]["kind"], "date")
        self.assertTrue(any(item["hide"] for item in payload))

    def test_invalid_column_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            Column("x", "X", "unsupported")

    def test_default_sort_is_newest_first(self):
        self.assertEqual(default_sort(), (SortSpec("created_at", True),))


class SortModelTests(unittest.TestCase):
    def test_parses_directions(self):
        specs = parse_sort_model(
            '[{"colId": "comment", "sort": "asc"}, '
            '{"colId": "created_at", "sort": "DESC"}]'
        )
        self.assertEqual(
            specs, (SortSpec("comment", False), SortSpec("created_at", True))
        )

    def test_empty_and_dict_payloads(self):
        self.assertEqual(parse_sort_model(None), ())
        self.assertEqual(parse_sort_model(""), ())
        # a column without an entry keeps the agGrid default direction
        self.assertEqual(parse_sort_model([{"colId": "actor"}]), (SortSpec("actor"),))

    def test_rejects_unknown_column(self):
        with self.assertRaises(QueryError):
            parse_sort_model('[{"colId": "nope", "sort": "asc"}]')

    def test_rejects_bad_direction(self):
        with self.assertRaises(QueryError):
            parse_sort_model('[{"colId": "comment", "sort": "sideways"}]')

    def test_rejects_broken_payloads(self):
        with self.assertRaises(QueryError):
            parse_sort_model("{not json")
        with self.assertRaises(QueryError):
            parse_sort_model('{"colId": "comment"}')
        with self.assertRaises(QueryError):
            parse_sort_model('["comment"]')


class FilterModelTests(unittest.TestCase):
    def test_text_filter(self):
        group = parse_filter_model(
            model(comment={"filterType": "text", "type": "contains", "filter": "pub"})
        )
        self.assertEqual(
            group, ConditionGroup("AND", (Condition("comment", "contains", "pub"),))
        )

    def test_combined_text_filter(self):
        group = parse_filter_model(
            model(
                comment={
                    "filterType": "text",
                    "operator": "OR",
                    "condition1": {"type": "startsWith", "filter": "a"},
                    "condition2": {"type": "endsWith", "filter": "z"},
                }
            )
        )
        combined = only_group(group)
        self.assertEqual(combined.operator, "OR")
        self.assertEqual(len(combined.conditions), 2)

    def test_multi_filter_flattens_and_ignores_empty_models(self):
        group = parse_filter_model(
            model(
                comment={
                    "filterType": "multi",
                    "filterModels": [
                        None,
                        {"filterType": "text", "type": "contains", "filter": "x"},
                    ],
                }
            )
        )
        self.assertEqual(len(group.conditions), 1)

    def test_set_filter(self):
        group = parse_filter_model(
            model(severity={"filterType": "set", "values": ["error", "critical"]})
        )
        self.assertEqual(
            only_condition(group),
            Condition("severity", "in", ["error", "critical"]),
        )

    def test_number_filter_and_range(self):
        group = parse_filter_model(
            model(
                schema_version={
                    "filterType": "number",
                    "type": "inRange",
                    "filter": 1,
                    "filterTo": 3,
                }
            )
        )
        condition = only_condition(group)
        self.assertEqual(condition.value, 1.0)
        self.assertEqual(condition.value_to, 3.0)

    def test_date_filter_extends_a_date_only_upper_bound(self):
        group = parse_filter_model(
            model(
                created_at={
                    "filterType": "date",
                    "type": "inRange",
                    "dateFrom": "2026-01-01",
                    "dateTo": "2026-01-02",
                }
            )
        )
        condition = only_condition(group)
        self.assertEqual(condition.value, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(
            condition.value_to, datetime(2026, 1, 2, 23, 59, 59, 999999, tzinfo=UTC)
        )

    def test_date_filter_accepts_iso_timestamps(self):
        group = parse_filter_model(
            model(
                created_at={
                    "filterType": "date",
                    "type": "greaterThan",
                    "dateFrom": "2026-01-01T10:00:00Z",
                }
            )
        )
        self.assertEqual(
            only_condition(group).value, datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        )

    def test_blank_operators(self):
        group = parse_filter_model(
            model(info_url={"filterType": "text", "type": "blank"})
        )
        self.assertEqual(only_condition(group), Condition("info_url", "blank"))

    def test_empty_model_matches_everything(self):
        group = parse_filter_model({})
        self.assertEqual(group, ConditionGroup("AND", ()))
        self.assertEqual(parse_filter_model(None), group)

    def test_rejects_unknown_column(self):
        with self.assertRaises(QueryError):
            parse_filter_model(model(nope={"type": "contains", "filter": "x"}))

    def test_rejects_unsupported_operator(self):
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment={"filterType": "text", "type": "inRange"}))
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "contains",
                        "filter": 1,
                    }
                )
            )

    def test_rejects_broken_values(self):
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "equals",
                        "filter": "x",
                    }
                )
            )
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(
                    created_at={
                        "filterType": "date",
                        "type": "equals",
                        "dateFrom": "yesterday",
                    }
                )
            )
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(comment={"filterType": "text", "type": "contains"})
            )
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment={"filterType": "nope"}))
        with self.assertRaises(QueryError):
            parse_filter_model(model(severity={"filterType": "set", "values": []}))

    def test_rejects_empty_combined_filters(self):
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment={"filterType": "text", "operator": "AND"}))
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(comment={"filterType": "multi", "filterModels": []})
            )
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(
                    comment={
                        "filterType": "text",
                        "operator": "XOR",
                        "condition1": {"type": "contains", "filter": "x"},
                    }
                )
            )

    def test_rejects_non_object_payloads(self):
        with self.assertRaises(QueryError):
            parse_filter_model("[1, 2]")
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment=[1]))


class EvaluationTests(unittest.TestCase):
    def check(self, entry, payload) -> bool:
        return matches(entry, parse_filter_model(payload))

    def test_text_operators(self):
        entry = make_entry(comment="Content Published")
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={"filterType": "text", "type": "contains", "filter": "pub"}
                ),
            )
        )
        self.assertFalse(
            self.check(
                entry,
                model(
                    comment={"filterType": "text", "type": "contains", "filter": "zzz"}
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={
                        "filterType": "text",
                        "type": "notContains",
                        "filter": "zzz",
                    }
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={
                        "filterType": "text",
                        "type": "equals",
                        "filter": "content published",
                    }
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={
                        "filterType": "text",
                        "type": "notEqual",
                        "filter": "other",
                    }
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={
                        "filterType": "text",
                        "type": "startsWith",
                        "filter": "CONTENT",
                    }
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    comment={
                        "filterType": "text",
                        "type": "endsWith",
                        "filter": "published",
                    }
                ),
            )
        )

    def test_blank_handling(self):
        self.assertTrue(
            self.check(
                make_entry(info_url=None),
                model(info_url={"filterType": "text", "type": "blank"}),
            )
        )
        self.assertTrue(
            self.check(
                make_entry(info_url="  "),
                model(info_url={"filterType": "text", "type": "blank"}),
            )
        )
        self.assertTrue(
            self.check(
                make_entry(info_url="http://example.org/@@persistent-log"),
                model(info_url={"filterType": "text", "type": "notBlank"}),
            )
        )
        self.assertFalse(
            self.check(
                make_entry(), model(info_url={"filterType": "text", "type": "notBlank"})
            )
        )
        # a text comparison never matches a blank value
        self.assertFalse(
            self.check(
                make_entry(info_url=None),
                model(
                    info_url={"filterType": "text", "type": "notEqual", "filter": "x"}
                ),
            )
        )

    def test_details_filter_uses_the_canonical_json(self):
        self.assertTrue(
            self.check(
                make_entry(),
                model(
                    details={
                        "filterType": "text",
                        "type": "contains",
                        "filter": "source",
                    }
                ),
            )
        )
        self.assertFalse(
            self.check(
                make_entry(details_raw=None, details=None),
                model(details={"filterType": "text", "type": "notBlank"}),
            )
        )

    def test_set_filter_matches_exact_values(self):
        entry = make_entry(severity="error")
        self.assertTrue(
            self.check(
                entry, model(severity={"filterType": "set", "values": ["error"]})
            )
        )
        self.assertFalse(
            self.check(
                entry, model(severity={"filterType": "set", "values": ["ERROR"]})
            )
        )

    def test_number_filters(self):
        entry = make_entry(schema_version=3)
        for operator, value, expected in (
            ("equals", 3, True),
            ("notEqual", 3, False),
            ("lessThan", 4, True),
            ("lessThanOrEqual", 3, True),
            ("greaterThan", 2, True),
            ("greaterThanOrEqual", 4, False),
        ):
            with self.subTest(operator=operator):
                self.assertEqual(
                    self.check(
                        entry,
                        model(
                            schema_version={
                                "filterType": "number",
                                "type": operator,
                                "filter": value,
                            }
                        ),
                    ),
                    expected,
                )
        self.assertTrue(
            self.check(
                entry,
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "inRange",
                        "filter": 1,
                        "filterTo": 5,
                    }
                ),
            )
        )
        self.assertFalse(
            self.check(
                entry,
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "inRange",
                        "filter": 4,
                        "filterTo": 5,
                    }
                ),
            )
        )
        self.assertTrue(
            self.check(
                entry,
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "greaterThan",
                        "filter": 1,
                    }
                ),
            )
        )
        self.assertFalse(
            self.check(
                make_entry(schema_version=None),
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "equals",
                        "filter": 3,
                    }
                ),
            )
        )
        self.assertFalse(
            self.check(
                make_entry(schema_version="abc"),
                model(
                    schema_version={
                        "filterType": "number",
                        "type": "lessThan",
                        "filter": 5,
                    }
                ),
            )
        )

    def test_date_filters(self):
        entry = make_entry(created_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC))

        def payload(operator, **extra):
            return model(created_at={"filterType": "date", "type": operator, **extra})

        self.assertTrue(
            self.check(entry, payload("equals", dateFrom="2026-03-15T12:00:00+00:00"))
        )
        self.assertTrue(self.check(entry, payload("notEqual", dateFrom="2026-03-16")))
        self.assertTrue(self.check(entry, payload("lessThan", dateFrom="2026-03-16")))
        self.assertTrue(
            self.check(entry, payload("lessThanOrEqual", dateFrom="2026-03-15"))
        )
        self.assertFalse(
            self.check(entry, payload("greaterThan", dateFrom="2026-03-16"))
        )
        self.assertTrue(
            self.check(entry, payload("greaterThanOrEqual", dateFrom="2026-03-15"))
        )
        self.assertTrue(
            self.check(
                entry, payload("inRange", dateFrom="2026-01-01", dateTo="2026-12-31")
            )
        )
        self.assertFalse(self.check(entry, payload("inRange", dateFrom="2026-04-01")))

    def test_groups_and_missing_nodes(self):
        entry = make_entry(comment="published", actor="alice")
        payload = model(
            comment={
                "filterType": "text",
                "operator": "OR",
                "condition1": {"type": "equals", "filter": "published"},
                "condition2": {"type": "equals", "filter": "other"},
            },
            actor={"filterType": "text", "type": "equals", "filter": "bob"},
        )
        # AND across columns, OR inside the comment column
        self.assertFalse(self.check(entry, payload))
        self.assertTrue(matches(entry, None))
        self.assertTrue(matches(entry, ConditionGroup("OR", ())))
        self.assertTrue(
            matches(
                entry,
                ConditionGroup(
                    "AND",
                    (
                        Condition("comment", "contains", "pub"),
                        Condition("actor", "equals", "alice"),
                    ),
                ),
            )
        )
        self.assertFalse(
            matches(
                entry,
                ConditionGroup(
                    "AND",
                    (
                        Condition("comment", "contains", "pub"),
                        Condition("actor", "equals", "bob"),
                    ),
                ),
            )
        )

    def test_unhashable_handling_of_missing_conditions(self):
        with self.assertRaises(QueryError):
            matches(make_entry(), Condition("comment", "nope", "x"))


class SortingTests(unittest.TestCase):
    def test_sorting_by_text_and_date(self):
        entries = [
            make_entry(0, comment="beta", schema_version=2),
            make_entry(1, comment="Alpha", schema_version=3),
            make_entry(2, comment=None, schema_version=1),
        ]
        by_comment = sort_entries(entries, (SortSpec("comment"),))
        self.assertEqual(
            [entry["comment"] for entry in by_comment], [None, "Alpha", "beta"]
        )
        by_comment_desc = sort_entries(entries, (SortSpec("comment", True),))
        self.assertEqual(
            [entry["comment"] for entry in by_comment_desc], ["beta", "Alpha", None]
        )
        by_version = sort_entries(entries, (SortSpec("schema_version", True),))
        self.assertEqual([entry["schema_version"] for entry in by_version], [3, 2, 1])

    def test_multi_column_sorting(self):
        first = make_entry(0, comment="same")
        second = make_entry(1, comment="same")
        ordered = sort_entries(
            [first, second],
            (SortSpec("comment"), SortSpec("created_at", True)),
        )
        self.assertEqual(ordered, [second, first])

    def test_equal_records_keep_the_incoming_order(self):
        # the storage layer hands records over chronologically, so a stable
        # sort leaves full ties in chronological order on both backends
        same_date = datetime(2026, 1, 1, tzinfo=UTC)
        a = make_entry(0, created_at=same_date, comment="x")
        b = make_entry(0, created_at=same_date, comment="x")
        self.assertEqual(sort_entries([a, b], (SortSpec("comment"),)), [a, b])
        self.assertEqual(sort_entries([b, a], (SortSpec("comment"),)), [b, a])

    def test_invalid_number_sort_value(self):
        entries = [make_entry(0, schema_version="abc"), make_entry(1, schema_version=1)]
        ordered = sort_entries(entries, (SortSpec("schema_version"),))
        self.assertEqual(ordered[0]["schema_version"], 1)


class QuickFilterTests(unittest.TestCase):
    def test_quick_filter_searches_several_fields(self):
        entry = make_entry(comment="content published", actor="alice")
        self.assertTrue(quick_matches(entry, ""))
        self.assertTrue(quick_matches(entry, "PUBLISHED"))
        self.assertTrue(quick_matches(entry, "alice"))
        self.assertTrue(quick_matches(entry, "application"))
        self.assertTrue(quick_matches(entry, "content"))
        self.assertFalse(quick_matches(entry, "nothing"))


class ParserEdgeCaseTests(unittest.TestCase):
    """Guard rails of the payload validation."""

    def test_number_values_must_be_numbers(self):
        for value in (None, True, "x", ""):
            with self.subTest(value=value):
                with self.assertRaises(QueryError):
                    parse_filter_model(
                        model(
                            schema_version={
                                "filterType": "number",
                                "type": "equals",
                                "filter": value,
                            }
                        )
                    )

    def test_date_values_may_be_datetime_objects(self):
        # a caller may hand over a decoded model (the browser sends JSON)
        def payload(value):
            return {
                "created_at": {
                    "filterType": "date",
                    "type": "equals",
                    "dateFrom": value,
                }
            }

        aware = parse_filter_model(payload(datetime(2026, 1, 1, tzinfo=UTC)))
        self.assertEqual(only_condition(aware).value, datetime(2026, 1, 1, tzinfo=UTC))
        # a naive datetime is read as UTC
        naive = parse_filter_model(payload(datetime(2026, 1, 1)))
        self.assertEqual(only_condition(naive).value, datetime(2026, 1, 1, tzinfo=UTC))

    def test_column_specs_must_be_objects(self):
        with self.assertRaises(QueryError):
            query_model._condition(column_of("comment"), "nope")
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment="nope"))

    def test_combined_filter_without_conditions_is_rejected(self):
        with self.assertRaises(QueryError):
            parse_filter_model(
                model(comment={"filterType": "text", "condition2": None})
            )
        with self.assertRaises(QueryError):
            parse_filter_model(model(comment={"condition1": {}}))

    def test_locked_columns_are_rejected(self):
        locked = Column("locked", "Locked", TEXT, sortable=False, filterable=False)
        with patch.dict(query_model._BY_FIELD, {"locked": locked}):
            with self.assertRaises(QueryError):
                parse_sort_model('[{"colId": "locked", "sort": "asc"}]')
            with self.assertRaises(QueryError):
                parse_filter_model(
                    model(
                        locked={
                            "filterType": "text",
                            "type": "contains",
                            "filter": "x",
                        }
                    )
                )

    def test_text_values_may_be_datetimes(self):
        entry = make_entry(actor=datetime(2026, 1, 2, tzinfo=UTC))
        self.assertEqual(len(sort_entries([entry], (SortSpec("actor"),))), 1)
        self.assertTrue(
            matches(
                entry,
                parse_filter_model(
                    model(
                        actor={
                            "filterType": "text",
                            "type": "contains",
                            "filter": "2026-01-02",
                        }
                    )
                ),
            )
        )

    def test_missing_record_keys_count_as_blank(self):
        self.assertTrue(matches({}, Condition("info_url", "blank")))
        self.assertFalse(matches({}, Condition("info_url", "notBlank")))

    def test_number_sorting_handles_missing_values(self):
        missing = make_entry(0, schema_version=None)
        present = make_entry(1, schema_version=1)
        ordered = sort_entries([present, missing], (SortSpec("schema_version"),))
        self.assertEqual(ordered, [missing, present])


def test_suite():
    loader = unittest.defaultTestLoader
    return unittest.TestSuite(
        loader.loadTestsFromTestCase(test_case)
        for test_case in (
            ColumnsTests,
            SortModelTests,
            FilterModelTests,
            EvaluationTests,
            SortingTests,
            QuickFilterTests,
            ParserEdgeCaseTests,
        )
    )
