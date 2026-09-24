"""RDBMS backend: audit records stored in a relational database.

The persistence layer is implemented on top of **SQLModel**, the SQLAlchemy
based model layer from the FastAPI ecosystem: the tables below are
``table=True`` SQLModel models, the session and query helpers come from
``sqlmodel`` and everything underneath is still plain SQLAlchemy.  PostgreSQL
is the supported production target and the backend the test suite exercises in
a container; any other SQLAlchemy dialect works as well, because SQLModel
reuses SQLAlchemy's dialects and engines.  The schema is created on first use,
which keeps the deployment story to "set the URL in the control panel".

Differences to the ZODB backend are deliberate and documented:

* every write is committed immediately, so an audit record survives a
  later abort of the surrounding ZODB transaction,
* ``details`` and the governance payload must be JSON serializable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    Text,
    and_,
    cast,
    delete,
    func,
    not_,
    or_,
)
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select, text

from ..models import DeletionPreview, RetentionPolicy
from ..serialization import canonical_json
from .base import (
    BaseLogStorage,
    StorageConfigurationError,
    event_date,
    event_id_of,
    object_uid,
    severity_value,
)
from .query import (
    DATE,
    NUMBER,
    TEXT,
    Condition,
    ConditionGroup,
    QueryError,
    SearchResult,
    SortSpec,
    column_of,
)
from .query import JSON as JSON_KIND
from .query import Column as GridColumn

__all__ = [
    "Base",
    "EventRecord",
    "GovernanceRecord",
    "PolicyRecord",
    "PreviewRecord",
    "SQLRepository",
    "check_connection",
    "dispose_engines",
    "get_engine",
    "order_by_spec",
    "quick_expression",
    "sql_condition_tree",
]

TABLE_PREFIX = "persistentlogger_"

#: Alias kept for callers that used the SQLAlchemy declarative base name.
Base = SQLModel

#: Keys of a governance record that own a dedicated column.
_RESERVED_GOVERNANCE_KEYS = frozenset(
    {
        "event_id",
        "created_at",
        "actor",
        "action",
        "reason",
        "previous_digest",
        "integrity_digest",
    }
)


class EventRecord(SQLModel, table=True):
    """One audit event."""

    __tablename__ = f"{TABLE_PREFIX}events"

    event_id: str = Field(default="", max_length=36, primary_key=True)
    object_uid: str = Field(default="", max_length=1024, index=True)
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), index=True, nullable=False),
    )
    actor: str = Field(default="", max_length=255)
    event_type: str = Field(default="application", max_length=100)
    severity: str = Field(default="info", max_length=32)
    target: str = Field(default="", max_length=2048)
    comment: str = Field(default="", sa_column=Column(Text, nullable=False))
    info_url: str | None = Field(default=None, max_length=2048, nullable=True)
    details: Any = Field(default=None, sa_column=Column(JSON, nullable=True))
    schema_version: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    previous_digest: str = Field(default="", max_length=64)
    integrity_digest: str = Field(default="", max_length=64)


class GovernanceRecord(SQLModel, table=True):
    """One governance journal entry."""

    __tablename__ = f"{TABLE_PREFIX}governance"

    event_id: str = Field(default="", max_length=36, primary_key=True)
    object_uid: str = Field(default="", max_length=1024, index=True)
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    actor: str = Field(default="", max_length=255)
    action: str = Field(default="", max_length=100)
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    payload: Any = Field(default=None, sa_column=Column(JSON, nullable=True))
    previous_digest: str = Field(default="", max_length=64)
    integrity_digest: str = Field(default="", max_length=64)


class PolicyRecord(SQLModel, table=True):
    """Retention policy of a single object."""

    __tablename__ = f"{TABLE_PREFIX}policies"

    object_uid: str = Field(default="", max_length=1024, primary_key=True)
    enabled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    older_than_days: int = Field(default=365, sa_column=Column(Integer, nullable=False))
    max_entries: int = Field(default=100, sa_column=Column(Integer, nullable=False))


class PreviewRecord(SQLModel, table=True):
    """A stored deletion preview."""

    __tablename__ = f"{TABLE_PREFIX}previews"

    operation_id: str = Field(default="", max_length=36, primary_key=True)
    object_uid: str = Field(default="", max_length=1024, index=True)
    cutoff: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    event_ids: Any = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    selection_digest: str = Field(default="", max_length=64)


_engines: dict[str, Engine] = {}
_engines_lock = Lock()


def get_engine(database_url: str) -> Engine:
    """Return the (cached) engine for a database URL, creating the schema."""
    engine = _engines.get(database_url)
    if engine is None:
        with _engines_lock:
            engine = _engines.get(database_url)
            if engine is None:
                engine = create_engine(
                    database_url,
                    pool_pre_ping=True,
                    json_serializer=canonical_json,
                )
                SQLModel.metadata.create_all(engine)
                _engines[database_url] = engine
    return engine


def dispose_engines() -> None:
    """Dispose every cached engine (used by tests and at shutdown)."""
    with _engines_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


def check_connection(database_url: str) -> None:
    """Raise when the configured database cannot be reached."""
    with get_engine(database_url).connect() as connection:
        connection.execute(text("SELECT 1"))


def _as_utc(value: datetime) -> datetime:
    """Return a timezone aware UTC timestamp.

    PostgreSQL returns timezone aware values, other dialects may not.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def event_to_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a canonical event record onto database columns."""
    return {
        "event_id": event_id_of(entry),
        "created_at": event_date(entry),
        "actor": str(entry.get("username", entry.get("actor", "")) or ""),
        "event_type": str(entry.get("event_type", "application") or ""),
        "severity": severity_value(entry),
        "target": str(entry.get("target", "") or ""),
        "comment": str(entry.get("comment", "") or ""),
        "info_url": entry.get("info_url"),
        "details": entry.get("details_raw", entry.get("details")),
        "schema_version": int(entry.get("schema_version", 1) or 1),
        "previous_digest": str(entry.get("previous_digest", "") or ""),
        "integrity_digest": str(entry.get("integrity_digest", "") or ""),
    }


def event_to_entry(record: EventRecord) -> dict[str, Any]:
    """Map a database row back onto the canonical event record shape."""
    created_at = _as_utc(record.created_at)
    details = record.details
    return {
        "event_id": record.event_id,
        "created_at": created_at,
        "actor": record.actor,
        "event_type": record.event_type,
        "severity": record.severity,
        "target": record.target,
        "comment": record.comment,
        "info_url": record.info_url,
        "details": details,
        "schema_version": record.schema_version,
        "integrity_digest": record.integrity_digest,
        "uuid": record.event_id,
        "date": created_at,
        "username": record.actor,
        "level": record.severity,
        "details_raw": details,
        "previous_digest": record.previous_digest,
    }


def governance_to_entry(record: GovernanceRecord) -> dict[str, Any]:
    """Map a database row back onto the canonical governance record shape."""
    return {
        "event_id": record.event_id,
        "created_at": _as_utc(record.created_at),
        "actor": record.actor,
        "action": record.action,
        "reason": record.reason,
        **(record.payload or {}),
        "previous_digest": record.previous_digest,
        "integrity_digest": record.integrity_digest,
    }


#: Record columns backing the queryable grid columns.
_EVENT_COLUMNS: dict[str, Any] = {
    "created_at": EventRecord.created_at,
    "severity": EventRecord.severity,
    "actor": EventRecord.actor,
    "event_type": EventRecord.event_type,
    "target": EventRecord.target,
    "comment": EventRecord.comment,
    "info_url": EventRecord.info_url,
    "details": EventRecord.details,
    "schema_version": EventRecord.schema_version,
}

#: Columns the quick filter searches (mirrors ``query.quick_matches``).
_QUICK_COLUMNS = ("comment", "actor", "event_type", "target", "severity")


def _escape_like(value: str) -> str:
    """Escape a user supplied value for use inside a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _text_expression(column: GridColumn) -> Any:
    """Return the lower-cased text expression of a column."""
    record_column = _EVENT_COLUMNS[column.field]
    if column.kind == JSON_KIND:
        return func.lower(cast(record_column, Text))
    return func.lower(record_column)


def _blank_expression(column: GridColumn) -> Any:
    """Return an expression that is true for blank values of a column."""
    record_column = _EVENT_COLUMNS[column.field]
    if column.kind == TEXT:
        return or_(record_column.is_(None), func.trim(record_column) == "")
    return record_column.is_(None)


def _comparison(record_column: Any, condition: Condition) -> Any:
    """Translate a comparison operator into a SQLAlchemy expression."""
    operator = condition.operator
    value = condition.value
    if operator == "equals":
        return record_column == value
    if operator == "notEqual":
        return record_column != value
    if operator == "lessThan":
        return record_column < value
    if operator == "lessThanOrEqual":
        return record_column <= value
    if operator == "greaterThan":
        return record_column > value
    if operator == "greaterThanOrEqual":
        return record_column >= value
    if operator == "inRange":
        if condition.value_to is None:
            return record_column >= value
        return record_column.between(value, condition.value_to)
    raise QueryError(  # pragma: no cover - guarded while parsing
        f"unsupported operator {operator!r}"
    )


def _sql_condition(condition: Condition) -> Any:
    """Translate one parsed filter condition into a SQLAlchemy expression."""
    layout = column_of(condition.field)
    if layout is None:  # pragma: no cover - guarded while parsing
        raise QueryError(f"unknown filter column {condition.field!r}")
    record_column = _EVENT_COLUMNS[layout.field]
    operator = condition.operator
    if operator == "blank":
        return _blank_expression(layout)
    if operator == "notBlank":
        return not_(_blank_expression(layout))
    if operator == "in":
        return record_column.in_(condition.value)
    if layout.kind in {DATE, NUMBER}:
        return _comparison(record_column, condition)
    text_column = _text_expression(layout)
    wanted = str(condition.value)
    if operator == "equals":
        return text_column == wanted.casefold()
    if operator == "notEqual":
        return text_column != wanted.casefold()
    escaped = _escape_like(wanted.casefold())
    if operator == "contains":
        return text_column.like(f"%{escaped}%", escape="\\")
    if operator == "notContains":
        return not_(text_column.like(f"%{escaped}%", escape="\\"))
    if operator == "startsWith":
        return text_column.like(f"{escaped}%", escape="\\")
    if operator == "endsWith":
        return text_column.like(f"%{escaped}", escape="\\")
    raise QueryError(  # pragma: no cover - guarded while parsing
        f"unsupported operator {operator!r}"
    )


def sql_condition_tree(
    node: Condition | ConditionGroup | None,
) -> Any | None:
    """Translate a parsed filter model into a SQLAlchemy expression."""
    if node is None:
        return None
    if isinstance(node, Condition):
        return _sql_condition(node)
    parts = [
        part
        for part in (sql_condition_tree(child) for child in node.conditions)
        if part is not None
    ]
    if not parts:
        return None
    return or_(*parts) if node.operator == "OR" else and_(*parts)


def quick_expression(text: str) -> Any:
    """Translate the agGrid quick filter into a SQLAlchemy expression."""
    pattern = f"%{_escape_like(text.casefold())}%"
    return or_(
        *[
            func.lower(_EVENT_COLUMNS[field]).like(pattern, escape="\\")
            for field in _QUICK_COLUMNS
        ]
    )


def order_by_spec(spec: SortSpec) -> tuple[Any, ...]:
    """Return the ORDER BY clauses for one sort instruction.

    Text columns are ordered case insensitively and NULLs are placed the same
    way the Python query model places blank values, so both backends return
    identical orderings.
    """
    layout = column_of(spec.field)
    if layout is None:  # pragma: no cover - guarded while parsing
        raise QueryError(f"unknown sort column {spec.field!r}")
    if layout.kind == TEXT:
        order_column: Any = func.lower(_EVENT_COLUMNS[layout.field])
    elif layout.kind == JSON_KIND:
        order_column = func.lower(cast(_EVENT_COLUMNS[layout.field], Text))
    else:
        order_column = _EVENT_COLUMNS[layout.field]
    if spec.descending:
        clause = order_column.desc()
        if layout.kind in {TEXT, JSON_KIND}:
            clause = clause.nulls_last()
        return (clause,)
    clause = order_column.asc()
    if layout.kind in {TEXT, JSON_KIND}:
        clause = clause.nulls_first()
    return (clause,)


class SQLRepository(BaseLogStorage):
    """Audit log repository backed by a relational database."""

    def __init__(
        self,
        context: Any,
        database_url: str | None = None,
        engine: Engine | None = None,
    ):
        super().__init__(context)
        if engine is None:
            if not database_url:
                raise StorageConfigurationError(
                    "the RDBMS audit log storage backend requires a database URL"
                )
            engine = get_engine(database_url)
        self.engine = engine
        self.uid = object_uid(context)

    # ------------------------------------------------------------------
    # event primitives
    # ------------------------------------------------------------------
    def _load_events(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(EventRecord).where(EventRecord.object_uid == self.uid)
            ).all()
        return [event_to_entry(record) for record in records]

    def _load_event(self, event_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            record = session.get(EventRecord, event_id)
            if record is None or record.object_uid != self.uid:
                return None
            return event_to_entry(record)

    def search(
        self,
        conditions: Condition | ConditionGroup | None = None,
        sort: tuple[SortSpec, ...] = (),
        offset: int = 0,
        limit: int | None = None,
        quick: str = "",
    ) -> SearchResult:
        """Run the query in the database: filtering, sorting and paging in SQL."""
        criteria: list[Any] = [EventRecord.object_uid == self.uid]
        condition = sql_condition_tree(conditions)
        if condition is not None:
            criteria.append(condition)
        if quick:
            criteria.append(quick_expression(quick))

        statement = select(EventRecord).where(*criteria)
        # chronological order is the tie break of every sort, mirroring the
        # stable sort of the python query model
        ordering: list[Any] = [
            clause for spec in sort for clause in order_by_spec(spec)
        ]
        ordering.extend((EventRecord.created_at, EventRecord.event_id))
        statement = statement.order_by(*ordering)
        offset = max(offset, 0)
        if limit is None:
            if offset:
                statement = statement.offset(offset)
        else:
            statement = statement.offset(offset).limit(max(limit, 0))

        total_statement = select(func.count()).select_from(EventRecord).where(*criteria)
        with Session(self.engine) as session:
            records = session.exec(statement).all()
            total = int(session.exec(total_statement).one())
        return SearchResult(tuple(event_to_entry(record) for record in records), total)

    def _store_event(self, entry: dict[str, Any]) -> None:
        row = event_to_row(entry)
        with Session(self.engine) as session, session.begin():
            session.merge(EventRecord(object_uid=self.uid, **row))

    def _delete_events(self, event_ids: tuple[UUID, ...]) -> tuple[int, int]:
        keys = [str(event_id) for event_id in event_ids]
        if not keys:
            return (0, 0)
        with Session(self.engine) as session, session.begin():
            result = session.exec(
                delete(EventRecord).where(
                    EventRecord.object_uid == self.uid,
                    EventRecord.event_id.in_(keys),
                )
            )
        deleted = int(result.rowcount or 0)
        return deleted, len(keys) - deleted

    def _remove_all_events(self) -> None:
        with Session(self.engine) as session, session.begin():
            session.exec(delete(EventRecord).where(EventRecord.object_uid == self.uid))

    # ------------------------------------------------------------------
    # governance journal primitives
    # ------------------------------------------------------------------
    def _load_journal(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(GovernanceRecord).where(GovernanceRecord.object_uid == self.uid)
            ).all()
        return [governance_to_entry(record) for record in records]

    def _store_governance(self, entry: dict[str, Any]) -> None:
        payload = {
            key: value
            for key, value in entry.items()
            if key not in _RESERVED_GOVERNANCE_KEYS
        }
        with Session(self.engine) as session, session.begin():
            session.merge(
                GovernanceRecord(
                    event_id=str(entry["event_id"]),
                    object_uid=self.uid,
                    created_at=_as_utc(entry["created_at"]),
                    actor=str(entry["actor"]),
                    action=str(entry["action"]),
                    reason=str(entry["reason"]),
                    payload=payload or None,
                    previous_digest=str(entry.get("previous_digest", "")),
                    integrity_digest=str(entry.get("integrity_digest", "")),
                )
            )

    # ------------------------------------------------------------------
    # retention primitives
    # ------------------------------------------------------------------
    def _load_policy(self) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            record = session.get(PolicyRecord, self.uid)
            if record is None:
                return None
            return {
                "enabled": record.enabled,
                "older_than_days": record.older_than_days,
                "max_entries": record.max_entries,
            }

    def _store_policy(self, policy: RetentionPolicy) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(
                PolicyRecord(
                    object_uid=self.uid,
                    enabled=policy.enabled,
                    older_than_days=policy.older_than_days,
                    max_entries=policy.max_entries,
                )
            )

    def _store_preview(self, preview: DeletionPreview) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(
                PreviewRecord(
                    operation_id=str(preview.operation_id),
                    object_uid=self.uid,
                    cutoff=_as_utc(preview.cutoff),
                    event_ids=[str(event_id) for event_id in preview.event_ids],
                    selection_digest=preview.selection_digest,
                )
            )

    def _load_preview(self, operation_id: str) -> DeletionPreview | None:
        with Session(self.engine) as session:
            record = session.get(PreviewRecord, operation_id)
            if record is None or record.object_uid != self.uid:
                return None
            return DeletionPreview(
                UUID(record.operation_id),
                record.object_uid,
                _as_utc(record.cutoff),
                tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
                record.selection_digest,
            )
