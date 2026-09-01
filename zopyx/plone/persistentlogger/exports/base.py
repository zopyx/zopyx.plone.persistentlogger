"""Common export dispatcher and limits."""

from __future__ import annotations

from typing import Any

from ..serialization import export_rows

MAX_ENTRIES = 100_000
MAX_BYTES = 1_000_000_000


def _check_size(data: bytes, max_bytes: int) -> bytes:
    if len(data) > max_bytes:
        raise ValueError("export exceeds the configured byte limit")
    return data


def export_events(
    events: list[Any],
    format: str,
    *,
    max_entries: int = MAX_ENTRIES,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    if len(events) > max_entries:
        raise ValueError("export exceeds the configured entry limit")
    rows = export_rows(events)
    if format == "json":
        from .json import render_json

        data = render_json(rows)
    elif format == "csv":
        from .csv import render_csv

        data = render_csv(rows)
    elif format == "xlsx":
        from .xlsx import render_xlsx

        data = render_xlsx(rows)
    elif format == "ods":
        from .ods import render_ods

        data = render_ods(rows)
    else:
        raise ValueError(f"unsupported export format: {format}")
    return _check_size(data, max_bytes)
