"""Operational integrity verification and health reporting.

The local hash chain is an integrity *detector*.  It is not an immutable
storage mechanism: an operator who can rewrite both records and the stored
head can rewrite the chain.  The report deliberately exposes this limitation
so monitoring cannot mistake a healthy local verification for an external
seal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .storage.base import event_id_of, verify_event_chain, verify_governance_chain

SUPPORTED_SCHEMA_VERSIONS = frozenset({0, 1})


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """Serializable result of one complete repository verification."""

    checked_at: datetime
    object_uid: str
    backend: str
    event_count: int
    governance_count: int
    event_chain_ok: bool
    governance_chain_ok: bool
    sequence_status: str
    sequence_ok: bool
    schema_versions: tuple[int, ...]
    unsupported_schema_versions: tuple[int, ...]
    external_seal_status: str
    warnings: tuple[str, ...]

    @property
    def schema_ok(self) -> bool:
        return not self.unsupported_schema_versions

    @property
    def ok(self) -> bool:
        return (
            self.event_chain_ok
            and self.governance_chain_ok
            and self.sequence_ok
            and self.schema_ok
        )

    @property
    def status(self) -> str:
        return "healthy" if self.ok else "unhealthy"

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-compatible report data for APIs and monitoring."""
        data = asdict(self)
        data["checked_at"] = self.checked_at.isoformat()
        data["schema_versions"] = list(self.schema_versions)
        data["unsupported_schema_versions"] = list(self.unsupported_schema_versions)
        data["warnings"] = list(self.warnings)
        data.update({"schema_ok": self.schema_ok, "ok": self.ok, "status": self.status})
        return data


def _chain_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Follow links from the first record without trusting timestamps."""
    if not records:
        return []
    by_previous = {str(row.get("previous_digest", "")): row for row in records}
    current = by_previous.get("")
    if current is None:
        return []
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    while current is not None:
        identifier = event_id_of(current)
        if identifier in seen:
            return []
        seen.add(identifier)
        ordered.append(current)
        current = by_previous.get(str(current.get("integrity_digest", "")))
    return ordered if len(ordered) == len(records) else []


def _sequence_status(records: list[dict[str, Any]]) -> tuple[str, bool, str | None]:
    sequences = [row.get("sequence") for row in records]
    if not records or all(sequence is None for sequence in sequences):
        return (
            "not_available",
            True,
            (
                "sequence verification is unavailable for legacy records"
                if records
                else None
            ),
        )
    if any(
        not isinstance(sequence, int) or isinstance(sequence, bool)
        for sequence in sequences
    ):
        return "invalid", False, "one or more event sequence values are invalid"
    ordered = _chain_order(records)
    if len(ordered) != len(records):
        return (
            "unverifiable",
            False,
            "event chain order is unavailable for sequence verification",
        )
    values = [int(row["sequence"]) for row in ordered]
    if any(left >= right for left, right in zip(values, values[1:])):
        return "non_monotone", False, "event sequence is not strictly increasing"
    if len(set(values)) != len(values):
        return "duplicate", False, "event sequence contains duplicates"
    return "verified", True, None


def verify_repository(repository: Any) -> IntegrityReport:
    """Verify event/governance chains and return an operational health report.

    ``repository`` is intentionally duck-typed so this verifier is usable by
    both storage backends and by management/monitoring integrations without
    exposing their persistence implementations.
    """
    events = list(repository.events())
    journal = list(repository.journal())
    event_chain_ok = verify_event_chain(events)
    governance_chain_ok = verify_governance_chain(journal)
    sequence_status, sequence_ok, sequence_warning = _sequence_status(events)
    versions = sorted(
        {
            int(row.get("schema_version", 1) or 1)
            for row in events
            if isinstance(row.get("schema_version", 1), int)
        }
    )
    unsupported = sorted(set(versions) - SUPPORTED_SCHEMA_VERSIONS)
    warnings = [
        warning
        for warning in (
            sequence_warning,
            "local hash verification does not prevent database or ZODB rewrites",
            (
                "external sealing is not configured; this report is not "
                "tamper-proof evidence"
            ),
        )
        if warning
    ]
    return IntegrityReport(
        checked_at=datetime.now(UTC),
        object_uid=str(repository.object_uid()),
        backend=type(repository).__name__,
        event_count=len(events),
        governance_count=len(journal),
        event_chain_ok=event_chain_ok,
        governance_chain_ok=governance_chain_ok,
        sequence_status=sequence_status,
        sequence_ok=sequence_ok,
        schema_versions=tuple(versions),
        unsupported_schema_versions=tuple(unsupported),
        external_seal_status="not_configured",
        warnings=tuple(warnings),
    )


__all__ = ["IntegrityReport", "SUPPORTED_SCHEMA_VERSIONS", "verify_repository"]
