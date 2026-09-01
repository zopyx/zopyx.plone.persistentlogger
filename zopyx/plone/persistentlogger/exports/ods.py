"""ODS export."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from .csv import COLUMNS, _cell


def render_ods(rows: list[dict[str, Any]]) -> bytes:
    try:
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableCell, TableRow
        from odf.text import P
    except ImportError as exc:
        raise RuntimeError("ODS export requires the ods extra") from exc
    document = OpenDocumentSpreadsheet()
    table = Table(name="events")
    header = TableRow()
    for column in COLUMNS:
        cell = TableCell(valuetype="string")
        cell.addElement(P(text=column))
        header.addElement(cell)
    table.addElement(header)
    for row in rows:
        table_row = TableRow()
        for column in COLUMNS:
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=_cell(row.get(column))))
            table_row.addElement(cell)
        table.addElement(table_row)
    document.spreadsheet.addElement(table)
    output = BytesIO()
    document.write(output)
    return output.getvalue()
