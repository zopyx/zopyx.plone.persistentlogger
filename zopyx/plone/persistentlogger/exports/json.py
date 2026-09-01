"""JSON export."""

from __future__ import annotations

import json
from typing import Any

from ..serialization import json_default


def render_json(rows: list[dict[str, Any]]) -> bytes:
    envelope = {"schema_version": 1, "records": rows}
    return json.dumps(
        envelope, default=json_default, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8")
