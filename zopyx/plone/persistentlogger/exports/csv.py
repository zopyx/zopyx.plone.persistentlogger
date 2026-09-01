"""CSV export with spreadsheet formula-injection protection."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..serialization import canonical_json, json_default

COLUMNS = [
    "event_id",
    "created_at",
    "actor",
    "event_type",
    "severity",
    "target",
    "comment",
    "info_url",
    "details",
    "schema_version",
    "integrity_digest",
]


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        value = canonical_json(value)
    elif value is None:
        value = ""
    elif not isinstance(value, str):
        value = json.dumps(value, default=json_default, ensure_ascii=False)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\r\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in COLUMNS})
    return stream.getvalue().encode("utf-8")
