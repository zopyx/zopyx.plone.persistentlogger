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

import atexit
import os
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
    inspect,
    not_,
    or_,
)
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Field, Session, SQLModel, create_engine, select, text

from ..models import DeletionPreview, DeletionResult, LogEvent, RetentionPolicy, utc_now
from ..serialization import canonical_json
from .base import (
    BaseLogStorage,
    StorageConfigurationError,
    StorageIntegrityError,
    event_date,
    event_digest,
    event_id_of,
    new_event_entry,
    new_governance_entry,
    next_sequence,
    object_uid,
    selection_digest,
    severity_value,
    verify_event_chain,
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
    "ChainHeadRecord",
    "SchemaVersionRecord",
    "PolicyRecord",
    "PreviewRecord",
    "SQLRepository",
    "check_connection",
    "dispose_engines",
    "get_engine",
    "register_engine_lifecycle",
    "order_by_spec",
    "quick_expression",
    "sql_condition_tree",
]

TABLE_PREFIX = "persistentlogger_"

POOL_SIZE_ENVIRONMENT_VARIABLE = "ZOPYX_PERSISTENTLOGGER_POOL_SIZE"
POOL_MAX_OVERFLOW_ENVIRONMENT_VARIABLE = "ZOPYX_PERSISTENTLOGGER_POOL_MAX_OVERFLOW"
POOL_TIMEOUT_ENVIRONMENT_VARIABLE = "ZOPYX_PERSISTENTLOGGER_POOL_TIMEOUT"
POOL_RECYCLE_ENVIRONMENT_VARIABLE = "ZOPYX_PERSISTENTLOGGER_POOL_RECYCLE"

DEFAULT_POOL_SIZE = 5
DEFAULT_POOL_MAX_OVERFLOW = 10
DEFAULT_POOL_TIMEOUT = 30
DEFAULT_POOL_RECYCLE = 1800

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
    object_uid: str = Field(default="", max_length=1024, primary_key=True)
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
    sequence: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    previous_digest: str = Field(default="", max_length=64)
    integrity_digest: str = Field(default="", max_length=64)


class GovernanceRecord(SQLModel, table=True):
    """One governance journal entry."""

    __tablename__ = f"{TABLE_PREFIX}governance"

    event_id: str = Field(default="", max_length=36, primary_key=True)
    object_uid: str = Field(default="", max_length=1024, primary_key=True)
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
    object_uid: str = Field(default="", max_length=1024, primary_key=True)
    cutoff: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    event_ids: Any = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    selection_digest: str = Field(default="", max_length=64)
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )


class ChainHeadRecord(SQLModel, table=True):
    """Per-object heads serialized with event and governance appends."""

    __tablename__ = f"{TABLE_PREFIX}chain_heads"

    object_uid: str = Field(default="", max_length=1024, primary_key=True)
    event_id: str = Field(default="", max_length=36)
    event_digest: str = Field(default="", max_length=64)
    governance_event_id: str = Field(default="", max_length=36)
    governance_digest: str = Field(default="", max_length=64)


class SchemaVersionRecord(SQLModel, table=True):
    """Version marker for additive RDBMS schema changes."""

    __tablename__ = f"{TABLE_PREFIX}schema_version"

    name: str = Field(default="audit", primary_key=True, max_length=64)
    version: int = Field(default=1, sa_column=Column(Integer, nullable=False))


SCHEMA_VERSION = 2


_engines: dict[str, Engine] = {}
_engines_lock = Lock()
_lifecycle_registered = False


def _pool_value(name: str, default: int) -> int:
    """Read and validate one integer pool setting from the environment."""
    raw_value = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise StorageConfigurationError(
            f"{name} must be an integer, got {raw_value!r}"
        ) from exc
    if value < 0:
        raise StorageConfigurationError(f"{name} must not be negative")
    return value


def _pool_options(database_url: str) -> dict[str, int]:
    """Return production pool settings, excluding SQLite's special pools."""
    if make_url(database_url).get_backend_name() == "sqlite":
        return {}
    return {
        "pool_size": _pool_value(POOL_SIZE_ENVIRONMENT_VARIABLE, DEFAULT_POOL_SIZE),
        "max_overflow": _pool_value(
            POOL_MAX_OVERFLOW_ENVIRONMENT_VARIABLE, DEFAULT_POOL_MAX_OVERFLOW
        ),
        "pool_timeout": _pool_value(
            POOL_TIMEOUT_ENVIRONMENT_VARIABLE, DEFAULT_POOL_TIMEOUT
        ),
        "pool_recycle": _pool_value(
            POOL_RECYCLE_ENVIRONMENT_VARIABLE, DEFAULT_POOL_RECYCLE
        ),
    }


def register_engine_lifecycle(event: Any = None) -> None:
    """Register one process-shutdown callback for cached engine disposal."""
    del event
    global _lifecycle_registered
    with _engines_lock:
        if _lifecycle_registered:
            return
        atexit.register(dispose_engines)
        _lifecycle_registered = True


def _schema_error(version: Any) -> StorageConfigurationError:
    """Return the stable error used for unsupported schema state."""
    return StorageConfigurationError(
        "incompatible persistent logger database schema: found version "
        f"{version!r}, expected version {SCHEMA_VERSION}; upgrade the "
        "persistent logger package or migrate the database before starting"
    )


def _upgrade_event_sequence_column(engine: Engine) -> None:
    """Add the nullable sequence column to pre-sequence installations."""
    columns = {
        column["name"]
        for column in inspect(engine).get_columns(str(EventRecord.__tablename__))
    }
    if "sequence" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                f'ALTER TABLE "{EventRecord.__tablename__}" ADD COLUMN sequence INTEGER'
            )
        )


def _upgrade_preview_expiry_column(engine: Engine) -> None:
    """Add the preview expiry column and its index to older installations."""
    columns = {
        column["name"]
        for column in inspect(engine).get_columns(str(PreviewRecord.__tablename__))
    }
    if "expires_at" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f'ALTER TABLE "{PreviewRecord.__tablename__}" '
                    "ADD COLUMN expires_at DATETIME"
                )
            )
    preview_table = SQLModel.metadata.tables[str(PreviewRecord.__tablename__)]
    with engine.begin() as connection:
        for index in preview_table.indexes:
            index.create(connection, checkfirst=True)


def _normalize_schema_marker(engine: Engine) -> None:
    """Normalize the pre-release ``key=main`` marker to ``name=audit``."""
    table_name = str(SchemaVersionRecord.__tablename__)
    columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
    if "key" in columns and "name" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(f'ALTER TABLE "{table_name}" RENAME COLUMN "key" TO "name"')
            )
        columns.remove("key")
        columns.add("name")
    if "name" not in columns or "version" not in columns:
        raise _schema_error(sorted(columns))
    with Session(engine) as session, session.begin():
        markers = session.exec(select(SchemaVersionRecord)).all()
        if len(markers) > 1:
            raise _schema_error([(marker.name, marker.version) for marker in markers])
        if markers and markers[0].name != "audit":
            markers[0].name = "audit"


def _ensure_schema_version(engine: Engine) -> None:
    _normalize_schema_marker(engine)
    with Session(engine) as session:
        marker = session.get(SchemaVersionRecord, "audit")
        if marker is not None and (
            not isinstance(marker.version, int)
            or marker.version < 0
            or marker.version > SCHEMA_VERSION
        ):
            raise _schema_error(marker.version)
    _upgrade_event_sequence_column(engine)
    _upgrade_preview_expiry_column(engine)
    with Session(engine) as session, session.begin():
        marker = session.get(SchemaVersionRecord, "audit")
        if marker is None:
            session.add(SchemaVersionRecord(name="audit", version=SCHEMA_VERSION))
        elif marker.version < SCHEMA_VERSION:
            marker.version = SCHEMA_VERSION


def get_engine(database_url: str) -> Engine:
    """Return the cached engine and apply additive compatible schema changes."""
    register_engine_lifecycle()
    engine = _engines.get(database_url)
    if engine is None:
        with _engines_lock:
            engine = _engines.get(database_url)
            if engine is None:
                engine = create_engine(
                    database_url,
                    pool_pre_ping=True,
                    json_serializer=canonical_json,
                    **_pool_options(database_url),
                )
                try:
                    SQLModel.metadata.create_all(engine)
                    _ensure_schema_version(engine)
                except Exception:
                    engine.dispose()
                    raise
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
        "details": entry.get("details"),
        "schema_version": int(entry.get("schema_version", 1) or 1),
        "sequence": entry.get("sequence"),
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
        "sequence": record.sequence,
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

    def _lock_namespace(self) -> str:
        return f"{type(self).__name__}:{id(self.engine)}"

    def _locked_head(self, session: Session) -> ChainHeadRecord:
        """Load and lock this object's chain head inside a write transaction."""
        head = session.exec(
            select(ChainHeadRecord)
            .where(ChainHeadRecord.object_uid == self.uid)
            .with_for_update()
        ).first()
        if head is not None:
            return head
        existing = session.scalars(
            select(EventRecord).where(EventRecord.object_uid == self.uid)
        ).all()
        digest = self._chain_tail([event_to_entry(row) for row in existing])
        head = ChainHeadRecord(object_uid=self.uid, event_digest=digest)
        session.add(head)
        session.flush()
        return head

    def append(self, event: LogEvent) -> dict[str, Any]:
        """Append an event to the persisted chain head atomically."""
        with self._lock("events"):
            with Session(self.engine) as session, session.begin():
                event_id = str(event.event_id)
                if session.get(EventRecord, (event_id, self.uid)) is not None:
                    raise ValueError(f"event id {event_id} already exists")
                head = self._locked_head(session)
                existing = session.scalars(
                    select(EventRecord).where(EventRecord.object_uid == self.uid)
                ).all()
                existing_entries = [event_to_entry(row) for row in existing]
                if existing_entries and not verify_event_chain(existing_entries):
                    raise StorageIntegrityError(
                        "cannot append to an unverifiable event chain; "
                        "migrate or repair it"
                    )
                expected_tail = self._chain_tail(existing_entries)
                if head.event_digest != expected_tail:
                    raise StorageIntegrityError(
                        "persisted event head does not match the event chain"
                    )
                entry = new_event_entry(
                    event,
                    expected_tail,
                    next_sequence(existing_entries),
                )
                session.add(EventRecord(object_uid=self.uid, **event_to_row(entry)))
                head.event_id = event_id
                head.event_digest = entry["integrity_digest"]
            return entry

    def _load_event_head(self) -> str:
        with Session(self.engine) as session:
            head = session.get(ChainHeadRecord, self.uid)
            return head.event_digest if head is not None else ""

    def _store_event_head(self, entry: dict[str, Any]) -> None:
        with Session(self.engine) as session, session.begin():
            head = self._locked_head(session)
            head.event_id = str(entry["event_id"])
            head.event_digest = str(entry["integrity_digest"])

    def _reset_event_head(self) -> None:
        with Session(self.engine) as session, session.begin():
            head = self._locked_head(session)
            records = session.scalars(
                select(EventRecord).where(EventRecord.object_uid == self.uid)
            ).all()
            head.event_digest = self._chain_tail(
                [event_to_entry(row) for row in records]
            )
            head.event_id = ""

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
            record = session.get(EventRecord, (event_id, self.uid))
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
        with Session(self.engine) as session, session.begin():
            total = int(session.exec(total_statement).one())
            records = session.exec(statement).all()
            rows = tuple(event_to_entry(record) for record in records)
        return SearchResult(rows, total)

    def _store_event(self, entry: dict[str, Any]) -> None:
        row = event_to_row(entry)
        with Session(self.engine) as session, session.begin():
            if session.get(EventRecord, (row["event_id"], self.uid)) is not None:
                raise ValueError(f"event id {row['event_id']} already exists")
            session.add(EventRecord(object_uid=self.uid, **row))

    def _rewrite_event(self, entry: dict[str, Any]) -> None:
        """Persist a changed digest during retention relinking or repair."""
        row = event_to_row(entry)
        with Session(self.engine) as session, session.begin():
            record = session.get(EventRecord, (row["event_id"], self.uid))
            if record is None:
                raise ValueError(f"event id {row['event_id']} does not exist")
            record.previous_digest = row["previous_digest"]
            record.integrity_digest = row["integrity_digest"]

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

    def _relink_after_delete(
        self, session: Session, event_ids: tuple[UUID, ...]
    ) -> tuple[int, int, str]:
        """Delete selected rows and relink the surviving rows in one transaction."""
        head = self._locked_head(session)
        rows = session.scalars(
            select(EventRecord).where(EventRecord.object_uid == self.uid)
        ).all()
        existing_entries = [event_to_entry(row) for row in rows]
        if (
            existing_entries
            and any(
                entry.get("previous_digest") or entry.get("integrity_digest")
                for entry in existing_entries
            )
            and not verify_event_chain(existing_entries)
        ):
            raise StorageIntegrityError(
                "cannot delete from an unverifiable event chain; migrate or repair it"
            )
        expected_tail = self._chain_tail(existing_entries)
        if head.event_digest != expected_tail:
            raise StorageIntegrityError(
                "persisted event head does not match the event chain"
            )
        selected = {str(event_id) for event_id in event_ids}
        keys = list(selected)
        result = (
            session.exec(
                delete(EventRecord).where(
                    EventRecord.object_uid == self.uid,
                    EventRecord.event_id.in_(keys),
                )
            )
            if keys
            else None
        )
        deleted = int(result.rowcount or 0) if result is not None else 0
        missing = len(keys) - deleted
        survivors = [
            entry
            for entry in self._chain_order([event_to_entry(row) for row in rows])
            if event_id_of(entry) not in selected
        ]
        anchor = ""
        previous = ""
        rows_by_id = {row.event_id: row for row in rows}
        if deleted:
            for entry in survivors:
                entry["previous_digest"] = previous
                entry["integrity_digest"] = event_digest(entry)
                row = rows_by_id[event_id_of(entry)]
                row.previous_digest = entry["previous_digest"]
                row.integrity_digest = entry["integrity_digest"]
                anchor = anchor or str(entry["integrity_digest"])
                previous = str(entry["integrity_digest"])
        else:
            previous = self._chain_tail(
                [
                    entry
                    for entry in (event_to_entry(row) for row in rows)
                    if event_id_of(entry) not in selected
                ]
            )
        head.event_id = event_id_of(survivors[-1]) if survivors else ""
        head.event_digest = previous
        return deleted, missing, anchor

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

    @staticmethod
    def _governance_model(
        entry: dict[str, Any], object_uid_value: str
    ) -> GovernanceRecord:
        """Build a governance row without opening a second session."""
        payload = {
            key: value
            for key, value in entry.items()
            if key not in _RESERVED_GOVERNANCE_KEYS
        }
        return GovernanceRecord(
            event_id=str(entry["event_id"]),
            object_uid=object_uid_value,
            created_at=_as_utc(entry["created_at"]),
            actor=str(entry["actor"]),
            action=str(entry["action"]),
            reason=str(entry["reason"]),
            payload=payload or None,
            previous_digest=str(entry.get("previous_digest", "")),
            integrity_digest=str(entry.get("integrity_digest", "")),
        )

    def _store_governance_in_session(
        self, session: Session, entry: dict[str, Any]
    ) -> None:
        """Add governance evidence to an existing transaction."""
        if (
            session.get(GovernanceRecord, (str(entry["event_id"]), self.uid))
            is not None
        ):
            raise ValueError(f"governance id {entry['event_id']} already exists")
        session.add(self._governance_model(entry, self.uid))

    def _store_governance(self, entry: dict[str, Any]) -> None:
        with Session(self.engine) as session, session.begin():
            self._store_governance_in_session(session, entry)

    def _store_governance_head_in_session(
        self, session: Session, entry: dict[str, Any]
    ) -> None:
        """Update governance head in an existing transaction."""
        head = self._locked_head(session)
        head.governance_event_id = str(entry["event_id"])
        head.governance_digest = str(entry["integrity_digest"])

    def _load_governance_head(self) -> str:
        with Session(self.engine) as session:
            head = session.get(ChainHeadRecord, self.uid)
            return head.governance_digest if head is not None else ""

    def _store_governance_head(self, entry: dict[str, Any]) -> None:
        with Session(self.engine) as session, session.begin():
            head = self._locked_head(session)
            head.governance_event_id = str(entry["event_id"])
            head.governance_digest = str(entry["integrity_digest"])

    def record_governance(
        self, action: str, actor: str, reason: str, **data: object
    ) -> dict[str, Any]:
        """Append governance metadata and its head in one transaction."""
        with self._lock("governance"):
            with Session(self.engine) as session, session.begin():
                head = self._locked_head(session)
                entry = new_governance_entry(
                    action, actor, reason, head.governance_digest, **data
                )
                payload = {
                    key: value
                    for key, value in entry.items()
                    if key not in _RESERVED_GOVERNANCE_KEYS
                }
                session.add(
                    GovernanceRecord(
                        event_id=str(entry["event_id"]),
                        object_uid=self.uid,
                        created_at=_as_utc(entry["created_at"]),
                        actor=str(entry["actor"]),
                        action=str(entry["action"]),
                        reason=str(entry["reason"]),
                        payload=payload or None,
                        previous_digest=str(entry["previous_digest"]),
                        integrity_digest=str(entry["integrity_digest"]),
                    )
                )
                head.governance_event_id = str(entry["event_id"])
                head.governance_digest = str(entry["integrity_digest"])
            return entry

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

    @staticmethod
    def _preview_from_record(record: PreviewRecord) -> DeletionPreview:
        return DeletionPreview(
            UUID(record.operation_id),
            record.object_uid,
            _as_utc(record.cutoff),
            tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
            record.selection_digest,
            _as_utc(record.expires_at) if record.expires_at is not None else None,
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
                    expires_at=(
                        None
                        if preview.expires_at is None
                        else _as_utc(preview.expires_at)
                    ),
                )
            )

    def cleanup_expired_previews(self, now: datetime | None = None) -> int:
        """Delete expired previews for this object only."""
        current = _as_utc(now or utc_now())
        with Session(self.engine) as session, session.begin():
            expires_at = getattr(PreviewRecord, "expires_at")
            result = session.exec(
                delete(PreviewRecord).where(
                    getattr(PreviewRecord, "object_uid") == self.uid,
                    or_(expires_at.is_(None), expires_at <= current),
                )
            )
            return int(result.rowcount or 0)

    def remove_object(self) -> int:
        """Delete all external audit rows owned by this exact object UID."""
        models = (
            EventRecord,
            GovernanceRecord,
            PolicyRecord,
            PreviewRecord,
            ChainHeadRecord,
        )
        with self._lock("events"), self._lock("governance"):
            with Session(self.engine) as session, session.begin():
                deleted = 0
                for model in models:
                    result = session.exec(
                        delete(model).where(getattr(model, "object_uid") == self.uid)
                    )
                    deleted += int(result.rowcount or 0)
                return deleted

    def _consume_preview(self, preview: DeletionPreview) -> DeletionPreview | None:
        with Session(self.engine) as session, session.begin():
            record = session.get(PreviewRecord, (str(preview.operation_id), self.uid))
            if record is None:
                return None
            stored = DeletionPreview(
                UUID(record.operation_id),
                record.object_uid,
                _as_utc(record.cutoff),
                tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
                record.selection_digest,
                None if record.expires_at is None else _as_utc(record.expires_at),
            )
            if stored != preview:
                return None
            session.delete(record)
            return stored

    def delete_preview(
        self,
        preview: DeletionPreview,
        reason: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Consume preview, delete events, and write governance atomically."""
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        current = _as_utc(now) if now is not None else None
        if current is not None:
            self.cleanup_expired_previews(current)
        with self._lock("events"):
            with Session(self.engine) as session, session.begin():
                record = session.get(
                    PreviewRecord, (str(preview.operation_id), self.uid)
                )
                if record is None:
                    raise ValueError("deletion preview is missing or stale")
                stored = DeletionPreview(
                    UUID(record.operation_id),
                    record.object_uid,
                    _as_utc(record.cutoff),
                    tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
                    record.selection_digest,
                    None if record.expires_at is None else _as_utc(record.expires_at),
                )
                if (
                    stored != preview
                    or stored.object_uid != self.uid
                    or stored.selection_digest
                    != selection_digest(
                        stored.object_uid, stored.event_ids, stored.cutoff
                    )
                ):
                    raise ValueError("deletion preview is missing or stale")
                session.delete(record)
                deleted, missing, _ = self._relink_after_delete(
                    session, stored.event_ids
                )
            return self._deletion_result(stored, reason, deleted, missing)

    def delete_and_journal(
        self,
        preview: DeletionPreview,
        reason: str,
        actor: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        """Delete and record retention evidence in one database transaction."""
        if len(reason.strip()) < 10:
            raise ValueError("deletion reason must contain at least 10 characters")
        current = _as_utc(now) if now is not None else None
        if current is not None:
            self.cleanup_expired_previews(current)
        with self._lock("events"), self._lock("governance"):
            with Session(self.engine) as session, session.begin():
                record = session.get(
                    PreviewRecord, (str(preview.operation_id), self.uid)
                )
                if record is None:
                    raise ValueError("deletion preview is missing or stale")
                stored = DeletionPreview(
                    UUID(record.operation_id),
                    record.object_uid,
                    _as_utc(record.cutoff),
                    tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
                    record.selection_digest,
                    None if record.expires_at is None else _as_utc(record.expires_at),
                )
                if (
                    stored != preview
                    or stored.object_uid != self.uid
                    or stored.selection_digest
                    != selection_digest(
                        stored.object_uid, stored.event_ids, stored.cutoff
                    )
                ):
                    raise ValueError("deletion preview is missing or stale")

                # Lock the shared head before reading the governance tail and
                # keep this row attached to the same transaction throughout.
                head = self._locked_head(session)
                session.delete(record)
                deleted, missing, survivor_digest = self._relink_after_delete(
                    session, stored.event_ids
                )
                result = self._deletion_result(stored, reason, deleted, missing)
                entry = new_governance_entry(
                    "retention_delete",
                    actor,
                    reason,
                    head.governance_digest,
                    operation_id=str(result.operation_id),
                    requested=result.requested,
                    eligible=result.eligible,
                    deleted=result.deleted,
                    missing=result.missing,
                    failed=result.failed,
                    survivor_digest=survivor_digest,
                )
                self._store_governance_in_session(session, entry)
                self._store_governance_head_in_session(session, entry)
            return result

    def _load_preview(self, operation_id: str) -> DeletionPreview | None:
        with Session(self.engine) as session:
            record = session.get(PreviewRecord, (operation_id, self.uid))
            if record is None or record.object_uid != self.uid:
                return None
            return DeletionPreview(
                UUID(record.operation_id),
                record.object_uid,
                _as_utc(record.cutoff),
                tuple(UUID(str(event_id)) for event_id in (record.event_ids or [])),
                record.selection_digest,
                None if record.expires_at is None else _as_utc(record.expires_at),
            )
