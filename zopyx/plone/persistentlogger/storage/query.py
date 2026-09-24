"""Backend independent query model for the audit log entries grid.

The entries view uses agGrid in *infinite row model* mode, i.e. paging, sorting
and filtering all happen on the server: the grid sends its ``sortModel`` and
``filterModel`` (plus ``startRow``/``endRow`` and the quick filter) and receives
exactly one page of rows together with the total number of matching records.

This module implements that query model once, so the browser, the ZODB backend
and the RDBMS backend cannot drift apart:

* :data:`COLUMNS` describes the queryable columns -- field name, label, value
  kind and the record keys it maps to.  The grid renders its column
  definitions from the same list the server validates against.
* :func:`parse_filter_model` / :func:`parse_sort_model` validate the agGrid
  payloads and turn them into :class:`Condition` / :class:`SortSpec` objects
  (:class:`QueryError` for anything unsupported).
* :func:`matches` / :func:`sort_entries` / :func:`quick_matches` evaluate a
  query against record dictionaries in pure Python (the ZODB backend and the
  shared contract tests).

Supported operations are the standard agGrid ones: text (``contains``,
``notContains``, ``equals``, ``notEqual``, ``startsWith``, ``endsWith``),
number/date (``equals``, ``notEqual``, ``lessThan``, ``lessThanOrEqual``,
``greaterThan``, ``greaterThanOrEqual``, ``inRange``), ``blank`` / ``notBlank``
for every kind, the set filter (``values``) for enumerations and agGrid's
combined filters (``operator: AND|OR`` with ``condition1``/``condition2`` and
the ``multi`` filter type).  Text comparisons are case insensitive, matching
the agGrid default.

Date bounds given as plain dates cover the whole day: a lower bound starts at
``2026-01-01 00:00:00``, an upper bound ends at ``2026-01-01 23:59:59.999999``,
so "before 2026-01-02" includes 2026-01-01.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from ..serialization import canonical_json
from .records import event_date, severity_value

__all__ = [
    "COLUMNS",
    "Condition",
    "ConditionGroup",
    "Column",
    "QueryError",
    "SearchResult",
    "SortSpec",
    "column_of",
    "columns_json",
    "matches",
    "parse_filter_model",
    "parse_sort_model",
    "quick_matches",
    "sort_entries",
]

TEXT = "text"
NUMBER = "number"
DATE = "date"
JSON = "json"

TEXT_OPERATORS = frozenset(
    {
        "contains",
        "notContains",
        "equals",
        "notEqual",
        "startsWith",
        "endsWith",
        "blank",
        "notBlank",
    }
)
COMPARISON_OPERATORS = frozenset(
    {
        "equals",
        "notEqual",
        "lessThan",
        "lessThanOrEqual",
        "greaterThan",
        "greaterThanOrEqual",
        "inRange",
        "blank",
        "notBlank",
    }
)
OPERATORS = {
    TEXT: TEXT_OPERATORS,
    JSON: TEXT_OPERATORS,
    NUMBER: COMPARISON_OPERATORS,
    DATE: COMPARISON_OPERATORS,
}


class QueryError(ValueError):
    """Raised for an agGrid query payload the server does not support."""


@dataclass(frozen=True, slots=True)
class Column:
    """One queryable column of the audit log."""

    field: str
    label: str
    kind: str = TEXT
    keys: tuple[str, ...] = ()
    sortable: bool = True
    filterable: bool = True
    hidden: bool = False

    def __post_init__(self) -> None:
        if self.kind not in OPERATORS:
            raise ValueError(f"unsupported column kind {self.kind!r}")
        if not self.keys:
            object.__setattr__(self, "keys", (self.field,))


#: Columns exposed to the grid.  The order is the column order of the grid.
COLUMNS: tuple[Column, ...] = (
    Column("created_at", "Date", DATE, ("created_at", "date")),
    Column("severity", "Level", TEXT, ("severity", "level")),
    Column("actor", "User", TEXT, ("actor", "username")),
    Column("event_type", "Type", TEXT),
    Column("target", "Target", TEXT),
    Column("comment", "Comment", TEXT),
    Column("info_url", "Info", TEXT),
    Column("details", "Details", JSON, ("details_raw", "details")),
    Column("schema_version", "Schema", NUMBER, hidden=True),
)

_BY_FIELD = {column.field: column for column in COLUMNS}

#: Fields the quick filter searches through.
_QUICK_FIELDS = ("comment", "actor", "username", "event_type", "target", "severity")


def column_of(field_name: str) -> Column | None:
    """Return the column definition for an agGrid ``colId``."""
    return _BY_FIELD.get(field_name)


def columns_json() -> str:
    """Return the column definitions as a JSON array (used by the template)."""
    return json.dumps(
        [
            {
                "field": column.field,
                "headerName": column.label,
                "kind": column.kind,
                "sortable": column.sortable,
                "filterable": column.filterable,
                "hide": column.hidden,
            }
            for column in COLUMNS
        ]
    )


@dataclass(frozen=True, slots=True)
class Condition:
    """A single filter condition on one column."""

    field: str
    operator: str
    value: Any = None
    value_to: Any = None


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    """A group of conditions combined with ``AND`` or ``OR``."""

    operator: str = "AND"
    conditions: tuple[Condition | ConditionGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class SortSpec:
    """A sort instruction on one column."""

    field: str
    descending: bool = False


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One page of matching records plus the total number of matches."""

    rows: tuple[dict[str, Any], ...] = ()
    total: int = 0
    fields: tuple[str, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------
def _load_json(raw: Any) -> Any:
    """Accept both a decoded object and a JSON string."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise QueryError(f"invalid JSON payload: {exc}") from exc


def parse_sort_model(raw: Any) -> tuple[SortSpec, ...]:
    """Turn an agGrid ``sortModel`` into validated sort instructions."""
    model = _load_json(raw)
    if not model:
        return ()
    if not isinstance(model, list):
        raise QueryError("sortModel must be a list")
    specs: list[SortSpec] = []
    for item in model:
        if not isinstance(item, dict):
            raise QueryError("sortModel entries must be objects")
        field_name = item.get("colId")
        column = _BY_FIELD.get(str(field_name))
        if column is None:
            raise QueryError(f"unknown sort column {field_name!r}")
        if not column.sortable:
            raise QueryError(f"column {column.field!r} is not sortable")
        direction = str(item.get("sort", "asc")).lower()
        if direction not in {"asc", "desc"}:
            raise QueryError(f"invalid sort direction {direction!r}")
        specs.append(SortSpec(column.field, direction == "desc"))
    return tuple(specs)


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise QueryError(f"column {field_name!r} expects a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise QueryError(f"column {field_name!r} expects a number") from exc


def _parse_date(value: Any, *, upper: bool) -> datetime | None:
    """Parse an agGrid date value into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
        date_only = False
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QueryError(f"invalid date value {value!r}") from exc
        date_only = len(text) == 10
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if date_only and upper:
        # "up to 2026-01-01" includes that whole day
        parsed = datetime.combine(parsed.date(), time.max, tzinfo=UTC)
    return parsed


def _condition(column: Column, spec: Any) -> Condition:
    """Validate one agGrid filter description for a column."""
    if not isinstance(spec, dict):
        raise QueryError(f"invalid filter for column {column.field!r}")
    filter_type = str(spec.get("filterType", "") or "")
    if filter_type == "set":
        values = spec.get("values")
        if not values:
            raise QueryError(f"set filter for {column.field!r} needs values")
        return Condition(column.field, "in", [str(value) for value in values])
    operator = str(spec.get("type", "") or "")
    if operator not in OPERATORS[column.kind]:
        raise QueryError(
            f"unsupported operator {operator!r} for column {column.field!r}"
        )
    if operator == "blank":
        return Condition(column.field, "blank")
    if operator == "notBlank":
        return Condition(column.field, "notBlank")
    if column.kind == NUMBER:
        if operator == "inRange":
            # like the date branch, a missing upper bound means "open ended"
            upper = spec.get("filterTo")
            return Condition(
                column.field,
                operator,
                _number(spec.get("filter"), column.field),
                None if upper in (None, "") else _number(upper, column.field),
            )
        return Condition(
            column.field, operator, _number(spec.get("filter"), column.field)
        )
    if column.kind == DATE:
        if operator == "inRange":
            return Condition(
                column.field,
                operator,
                _parse_date(spec.get("dateFrom"), upper=False),
                _parse_date(spec.get("dateTo"), upper=True),
            )
        # a date-only lower bound starts at the beginning of that day, a
        # date-only upper bound includes the whole day
        bound_is_upper = operator in {"lessThan", "lessThanOrEqual"}
        return Condition(
            column.field,
            operator,
            _parse_date(spec.get("dateFrom"), upper=bound_is_upper),
        )
    value = spec.get("filter")
    if value is None:
        raise QueryError(f"filter for column {column.field!r} needs a value")
    return Condition(column.field, operator, str(value))


def _flatten(column: Column, spec: Any) -> list[Condition | ConditionGroup]:
    """Turn one column's filter model into conditions (agGrid combined filters)."""
    if not isinstance(spec, dict):
        raise QueryError(f"invalid filter for column {column.field!r}")
    filter_type = str(spec.get("filterType", "") or "")
    if filter_type == "multi":
        # the "multi filter" combines different filter types per column
        models = [model for model in spec.get("filterModels") or [] if model]
        groups = [group for model in models for group in _flatten(column, model)]
        if not groups:
            raise QueryError(f"empty multi filter for column {column.field!r}")
        return groups
    if "condition1" in spec or "condition2" in spec:
        join = str(spec.get("operator", "AND") or "AND").upper()
        if join not in {"AND", "OR"}:
            raise QueryError(f"invalid filter operator {join!r}")
        parts = [
            _condition(column, spec[key])
            for key in ("condition1", "condition2")
            if spec.get(key)
        ]
        if not parts:
            raise QueryError(f"empty filter for column {column.field!r}")
        return [ConditionGroup(join, tuple(parts))]
    return [_condition(column, spec)]


def parse_filter_model(raw: Any) -> ConditionGroup:
    """Turn an agGrid ``filterModel`` into a validated condition group."""
    model = _load_json(raw)
    if not model:
        return ConditionGroup("AND", ())
    if not isinstance(model, dict):
        raise QueryError("filterModel must be an object")
    parts: list[Condition | ConditionGroup] = []
    for field_name, spec in model.items():
        column = _BY_FIELD.get(str(field_name))
        if column is None:
            raise QueryError(f"unknown filter column {field_name!r}")
        if not column.filterable:
            raise QueryError(f"column {column.field!r} is not filterable")
        parts.extend(_flatten(column, spec))
    return ConditionGroup("AND", tuple(parts))


# ----------------------------------------------------------------------
# evaluation
# ----------------------------------------------------------------------
def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    return str(value)


def _raw(entry: dict[str, Any], column: Column) -> Any:
    """Return the raw value of a column for one record."""
    for key in column.keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    if column.field in entry:
        return entry[column.field]
    return None


def entry_value(entry: dict[str, Any], column: Column) -> Any:
    """Return the comparable value of a column for one record."""
    if column.field == "created_at":
        return event_date(entry)
    if column.field == "severity":
        return severity_value(entry)
    return _raw(entry, column)


def _blank(value: Any) -> bool:
    """Return whether a value counts as blank.

    Only ``None`` and empty/whitespace strings are blank; an empty mapping is
    *not* blank, which keeps the answer identical on both backends (a SQL
    column can distinguish NULL from an empty JSON object).
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _contains(needle: str, haystack: str) -> bool:
    return needle.casefold() in haystack.casefold()


def _matches_condition(entry: dict[str, Any], condition: Condition) -> bool:
    column = _BY_FIELD.get(condition.field)
    if column is None:  # pragma: no cover - guarded while parsing
        raise QueryError(f"unknown filter column {condition.field!r}")
    value = entry_value(entry, column)
    operator = condition.operator

    if operator == "blank":
        return _blank(value)
    if operator == "notBlank":
        return not _blank(value)

    if column.kind == DATE:
        right = condition.value
        if right is None:
            return False
        left = event_date(entry)
        if operator == "equals":
            return left == right
        if operator == "notEqual":
            return left != right
        if operator == "lessThan":
            return left < right
        if operator == "lessThanOrEqual":
            return left <= right
        if operator == "greaterThan":
            return left > right
        if operator == "greaterThanOrEqual":
            return left >= right
        if operator == "inRange":
            end = condition.value_to
            return left >= right and (end is None or left <= end)
        raise QueryError(  # pragma: no cover - guarded while parsing
            f"unsupported operator {operator!r}"
        )

    if column.kind == NUMBER:
        if _blank(value):
            return False
        try:
            left_number = float(value)
        except (TypeError, ValueError):
            return False
        right_number = condition.value
        if operator == "equals":
            return left_number == right_number
        if operator == "notEqual":
            return left_number != right_number
        if operator == "lessThan":
            return left_number < right_number
        if operator == "lessThanOrEqual":
            return left_number <= right_number
        if operator == "greaterThan":
            return left_number > right_number
        if operator == "greaterThanOrEqual":
            return left_number >= right_number
        if operator == "inRange":
            end = condition.value_to
            return left_number >= right_number and (end is None or left_number <= end)
        raise QueryError(  # pragma: no cover - guarded while parsing
            f"unsupported operator {operator!r}"
        )

    text = _text(value)
    if operator == "in":
        return any(text == candidate for candidate in condition.value)
    if _blank(value):
        # a text comparison never matches a blank value in agGrid
        return False
    wanted = str(condition.value)
    if operator == "contains":
        return _contains(wanted, text)
    if operator == "notContains":
        return not _contains(wanted, text)
    if operator == "equals":
        return text.casefold() == wanted.casefold()
    if operator == "notEqual":
        return text.casefold() != wanted.casefold()
    if operator == "startsWith":
        return text.casefold().startswith(wanted.casefold())
    if operator == "endsWith":
        return text.casefold().endswith(wanted.casefold())
    raise QueryError(f"unsupported operator {operator!r}")


def matches(entry: dict[str, Any], node: Condition | ConditionGroup | None) -> bool:
    """Evaluate a parsed filter model against one record."""
    if node is None:
        return True
    if isinstance(node, Condition):
        return _matches_condition(entry, node)
    if not node.conditions:
        return True
    results = (matches(entry, child) for child in node.conditions)
    return all(results) if node.operator == "AND" else any(results)


def _sort_key(entry: dict[str, Any], spec: SortSpec) -> Any:
    """Return a comparable key; ``None`` sorts before any real value."""
    column = _BY_FIELD[spec.field]
    value = entry_value(entry, column)
    if column.kind == DATE:
        return event_date(entry)
    if column.kind == NUMBER:
        if value is None:
            # blank values sort first, exactly like the text columns
            return (0, 0.0)
        try:
            return (1, float(value))
        except (TypeError, ValueError):
            # unusable values are ordered last instead of raising
            return (2, 0.0)
    if value is None:
        return (0, "")
    return (1, _text(value).casefold())


def sort_entries(
    entries: list[dict[str, Any]], specs: tuple[SortSpec, ...]
) -> list[dict[str, Any]]:
    """Sort records by the given sort instructions.

    The sort is stable and each instruction is applied in turn (last one
    first), so records that are equal on *every* instruction keep the order
    they came in with -- the storage contract feeds them chronologically, which
    is exactly the tie break the SQL backend uses.
    """
    ordered = list(entries)
    for spec in reversed(specs):
        ordered.sort(key=lambda entry: _sort_key(entry, spec), reverse=spec.descending)
    return ordered


def quick_matches(entry: dict[str, Any], text: str) -> bool:
    """Evaluate agGrid's quick filter (substring across the text columns)."""
    if not text:
        return True
    needle = text.casefold()
    return any(_contains(needle, _text(entry.get(key))) for key in _QUICK_FIELDS)


def default_sort() -> tuple[SortSpec, ...]:
    """Return the default sort order of the entries grid (newest first)."""
    return (SortSpec("created_at", True),)
